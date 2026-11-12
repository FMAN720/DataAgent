"""Durable, versioned review gate between visual extraction and analysis tools."""

import asyncio
import csv
import hashlib
import io
import json
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from app.etl.chunker import DocumentChunker
from app.infrastructure.database.models import Document, DocumentChunk, VisionDraft, _utcnow
from app.vision.images import filename, inspect_source, page_png
from app.vision.types import PageResult, VisionError


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def expired(value):
    return value is None or value.replace(tzinfo=UTC) <= datetime.now(UTC)


def table_csv(table):
    # CSV is an intermediate literal format, not evaluated or opened as a spreadsheet.
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(table["columns"])
    writer.writerows(table["rows"])
    return stream.getvalue().encode("utf-8-sig")


def vision_operation(method):
    @wraps(method)
    async def scoped(self, *args, **kwargs):
        async with self.vision_session():
            return await method(self, *args, **kwargs)

    return scoped


class VisionService:
    def __init__(
        self, factory, settings, *, provider=None, datasets=None, knowledge=None, local_models=None
    ):
        self.factory, self.settings, self._provider = factory, settings, provider
        self.local_models = local_models
        self._vision_context = ContextVar("vision_snapshot", default=(False, None))
        self.datasets, self.knowledge = datasets, knowledge
        self._slot = asyncio.Semaphore(1)
        self._renders = asyncio.Semaphore(2)

    @property
    def provider(self):
        active, provider = self._vision_context.get()
        return provider if active else self._provider

    @property
    def recognition_enabled(self):
        return (
            self.local_models.available("vision")
            if self.local_models
            else self._provider is not None
        )

    @asynccontextmanager
    async def vision_session(self):
        if self._vision_context.get()[0] or self.local_models is None:
            yield
            return
        async with self.local_models.vision() as provider:
            token = self._vision_context.set((True, provider))
            try:
                yield
            finally:
                self._vision_context.reset(token)

    async def check_schema(self):
        async with self.factory() as session:
            await session.execute(select(VisionDraft.id).limit(0))

    @staticmethod
    def describe(item, *, include_pages=True):
        state = item.state or {}
        return {
            "id": item.id,
            "filename": item.filename,
            "mime_type": item.mime_type,
            "content_hash": item.content_hash,
            "page_count": item.page_count,
            "status": item.status,
            "revision": item.revision,
            "error": item.error,
            "model": state.get("model"),
            "selected_pages": state.get("selected_pages", []),
            "attempts": state.get("attempts", []),
            "pages": deepcopy(state.get("pages", {})) if include_pages else {},
            "publications": state.get("publications", []),
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    async def _locked(self, session, identifier, revision=None):
        item = await session.scalar(
            select(VisionDraft).where(VisionDraft.id == identifier).with_for_update()
        )
        if item is None:
            raise VisionError("识别草稿不存在", 404)
        if revision is not None and revision != item.revision:
            raise VisionError("草稿已更新，请重新读取后再操作", 409)
        return item

    async def stage(self, raw, name, mime):
        name, mime = filename(name, mime)
        async with self._renders:
            count = await asyncio.to_thread(inspect_source, raw, name, self.settings)
        identifier = str(uuid.uuid4())
        root = self.settings.vision_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / (identifier + Path(name).suffix.lower())
        try:
            with path.open("xb") as stream:
                stream.write(raw)
            async with self.factory() as session, session.begin():
                item = VisionDraft(
                    id=identifier,
                    filename=name,
                    mime_type=mime,
                    storage_path=str(path),
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    page_count=count,
                    status="previewed",
                    revision=0,
                    state={"pages": {}, "publications": []},
                )
                session.add(item)
                await session.flush()
                return self.describe(item)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def get(self, identifier):
        async with self.factory() as session, session.begin():
            item = await self._locked(session, identifier)
            if item.status == "recognizing" and expired(item.locked_until):
                item.status, item.error = (
                    "failed",
                    "上一轮识别已中断，已完成页面保留；重试可能产生费用",
                )
                item.lease_token = None
                item.revision += 1
            return self.describe(item)

    async def list(self):
        async with self.factory() as session:
            items = await session.scalars(
                select(VisionDraft).order_by(VisionDraft.created_at.desc()).limit(50)
            )
            return [self.describe(item, include_pages=False) for item in items]

    async def source(self, item):
        path = Path(item.storage_path)
        if (
            path.is_symlink()
            or path.resolve().parent != self.settings.vision_dir.resolve()
            or path.stem != item.id
            or path.suffix != Path(item.filename).suffix.lower()
        ):
            raise VisionError("识别来源路径不受项目管理")

        def read():
            with path.open("rb") as stream:
                return stream.read(self.settings.vision_max_bytes + 1)

        try:
            raw = await asyncio.to_thread(read)
        except OSError:
            raise VisionError("原始图片/扫描件不可读取") from None
        if (
            len(raw) > self.settings.vision_max_bytes
            or hashlib.sha256(raw).hexdigest() != item.content_hash
        ):
            raise VisionError("来源文件与上传记录不一致，已停止处理")
        return raw

    async def image(self, identifier, number):
        async with self.factory() as session:
            item = await session.get(VisionDraft, identifier)
            if not item:
                raise VisionError("识别草稿不存在", 404)
            raw = await self.source(item)
            async with self._renders:
                return await asyncio.to_thread(page_png, raw, item.filename, number, self.settings)

    async def _progress(
        self,
        identifier,
        token,
        *,
        number=None,
        result=None,
        usage=None,
        error=None,
        done=False,
        starting=False,
    ):
        async with self.factory() as session, session.begin():
            item = await self._locked(session, identifier)
            if item.lease_token != token:
                raise VisionError("识别请求已过期，结果未覆盖当前草稿", 409)
            state = deepcopy(item.state)
            if starting:
                state.setdefault("attempts", []).append(
                    {
                        "page_number": number,
                        "status": "submitted_or_unknown",
                        "started_at": _utcnow().isoformat(),
                    }
                )
            if result is not None:
                state["pages"][str(number)] = {
                    "raw": result,
                    "edited": deepcopy(result),
                    "usage": usage,
                    "reviewed": False,
                    "origin": "vision_api",
                    "model": self.provider.model,
                }
                state["attempts"][-1]["status"] = "completed"
            if error is not None or done:
                item.status = "failed" if error else "needs_review"
                item.error = error
                item.lease_token = None
                item.locked_until = None
            item.state = state
            item.revision += 1

    @vision_operation
    async def recognize(self, identifier, revision, pages, consent):
        if not consent:
            raise VisionError("识别前需明确同意发送所选页面至你配置的图片识别服务并承担 API 费用")
        if self.provider is None:
            raise VisionError("视觉 API 尚未启用，请配置独立模型和 Key", 503)
        if (
            not pages
            or len(pages) > self.settings.vision_max_pages_per_request
            or len(set(pages)) != len(pages)
        ):
            raise VisionError("页码为空、重复或超过单次识别页数上限")
        try:
            await asyncio.wait_for(self._slot.acquire(), timeout=1)
        except TimeoutError:
            raise VisionError("已有识别正在进行，请稍后重试", 503) from None
        token = str(uuid.uuid4())
        try:
            async with self.factory() as session, session.begin():
                item = await self._locked(session, identifier, revision)
                if any(type(n) is not int or n < 1 or n > item.page_count for n in pages):
                    raise VisionError("页码超出范围")
                if item.status == "recognizing" and not expired(item.locked_until):
                    raise VisionError("此草稿正在识别，请稍候", 409)
                raw = await self.source(item)
                name = item.filename
                state = deepcopy(item.state)
                if state.get("profile") not in (None, self.provider.profile):
                    raise VisionError("草稿已有其他模型的结果，请新建草稿避免混用", 409)
                state.update(
                    profile=self.provider.profile,
                    model=self.provider.model,
                    selected_pages=sorted(pages),
                )
                pending = [n for n in sorted(pages) if str(n) not in state["pages"]]
                item.state = state
                item.revision += 1
                if not pending:
                    item.status = "needs_review"
                    item.error = None
                else:
                    item.status = "recognizing"
                    item.error = None
                    item.lease_token = token
                    item.locked_until = _utcnow() + timedelta(
                        seconds=(self.settings.vision_timeout_seconds + 15) * len(pending) + 60
                    )
            if not pending:
                return await self.get(identifier)
            try:
                for number in pending:
                    async with self._renders:
                        png = await asyncio.to_thread(page_png, raw, name, number, self.settings)
                    await self._progress(identifier, token, number=number, starting=True)
                    result, usage = await self.provider.extract(png)
                    # Apply the same strict contract even to custom or test providers.
                    result = PageResult.model_validate(result).model_dump()
                    usage = {**usage, "image_sha256": hashlib.sha256(png).hexdigest()}
                    await self._progress(
                        identifier, token, number=number, result=result, usage=usage
                    )
                await self._progress(identifier, token, done=True)
            except BaseException as exc:
                error = (
                    str(exc)
                    if isinstance(exc, VisionError)
                    else "识别未完成，已完成页面保留；未自动重试，请核对费用和历史"
                )
                try:
                    await asyncio.shield(self._progress(identifier, token, error=error))
                except Exception as write_exc:  # noqa: BLE001 - 租约到期会恢复中断的写入；不能因此重新发起付费识别
                    logger.warning("识别失败状态未能保存: {}", type(write_exc).__name__)
                raise
            return await self.get(identifier)
        finally:
            self._slot.release()

    async def edit(self, identifier, revision, number, result, manual=False):
        result = PageResult.model_validate(result).model_dump()
        async with self.factory() as session, session.begin():
            item = await self._locked(session, identifier, revision)
            if item.status == "recognizing":
                raise VisionError("识别期间不能修改，请稍候", 409)
            state = deepcopy(item.state)
            page = state.get("pages", {}).get(str(number))
            if page is None:
                if not manual or not 1 <= number <= item.page_count:
                    raise VisionError("此页尚无识别结果；可明确选择人工转录")
                page = {
                    "raw": deepcopy(result),
                    "origin": "manual",
                    "model": None,
                    "usage": {"requests": 0},
                }
                state["pages"][str(number)] = page
                item.status = "needs_review"
            page["edited"] = result
            page["reviewed"] = False
            item.state = state
            item.revision += 1
            item.status = "needs_review"
            item.error = None
            return self.describe(item)

    def provenance(self, item, numbers):
        state = item.state
        return {
            "kind": "vision_reviewed",
            "draft_id": item.id,
            "filename": item.filename,
            "sha256": item.content_hash,
            "page_numbers": numbers,
            "total_pages": item.page_count,
            "partial_source": len(numbers) != item.page_count,
            "model": state.get("model"),
            "profile": state.get("profile"),
            "human_confirmed": True,
            "page_models": {str(n): state["pages"][str(n)].get("model") for n in numbers},
            "raw_result_hashes": {str(n): digest(state["pages"][str(n)]["raw"]) for n in numbers},
            "warning": "识别转录已由用户确认；不等于业务事实、完整性或因果关系已核验。",
        }

    def _reviewable(self, item, numbers, reviewed):
        if not reviewed:
            raise VisionError("请先逐项核对原图中的数字、单位、表头和页码，再确认")
        if item.status == "recognizing":
            raise VisionError("识别期间不能发布，请稍候", 409)
        if (
            not numbers
            or len(set(numbers)) != len(numbers)
            or len(numbers) > self.settings.vision_max_pages_per_request
        ):
            raise VisionError("请选择本次核对的页面")
        if any(str(n) not in item.state.get("pages", {}) for n in numbers):
            raise VisionError("所选页面未全部完成识别，未发布")

    async def table(self, identifier, revision, number, index, reviewed):
        if self.datasets is None:
            raise VisionError("表格导入服务尚未就绪", 503)
        async with self.factory() as session, session.begin():
            item = await self._locked(session, identifier, revision)
            self._reviewable(item, [number], reviewed)
            try:
                table = item.state["pages"][str(number)]["edited"]["tables"][index]
            except (KeyError, IndexError):
                raise VisionError("结果表不存在") from None
            if index < 0 or not table["rows"]:
                raise VisionError("结果表为空或编号无效")
            source = self.provenance(item, [number])
            source.update(table_index=index, table_title=table["title"], notes=table["notes"])
            await self.source(item)  # Do not publish from a missing/tampered image.
            # stage() is idempotent by bytes + options, including provenance.
            value = await self.datasets.stage(
                table_csv(table), f"识别表-第{number}页-{index + 1}.csv", provenance=source
            )
            state = deepcopy(item.state)
            key = "dataset:" + digest([number, index, table])
            state["publications"] = [p for p in state["publications"] if p["key"] != key] + [
                {"key": key, "kind": "dataset", "id": value["id"]}
            ]
            state["pages"][str(number)]["reviewed"] = True
            item.state = state
            item.revision += 1
            return {"draft": self.describe(item), "dataset": value}

    async def document(
        self,
        identifier,
        revision,
        numbers,
        reviewed,
        *,
        document_date=None,
        document_type="reference",
        allow_embedding=False,
    ):
        if allow_embedding and (self.knowledge is None or not self.knowledge.embedding_enabled):
            raise VisionError("向量索引未配置，本次没有发送文字", 503)
        async with self.factory() as session, session.begin():
            item = await self._locked(session, identifier, revision)
            self._reviewable(item, numbers, reviewed)
            numbers = sorted(numbers)
            source = self.provenance(item, numbers)
            segments = []
            for number in numbers:
                result = item.state["pages"][str(number)]["edited"]
                value = result["text"]
                for table in result["tables"]:
                    value += (
                        "\n\n"
                        + table["title"]
                        + "\n"
                        + table_csv(table).decode("utf-8-sig")
                        + "\n"
                        + table["notes"]
                    )
                if result["warnings"]:
                    value += "\n识别限制：" + "；".join(result["warnings"])
                if value.strip():
                    segments.append((number, value))
            if not segments or sum(len(value) for _, value in segments) > 500_000:
                raise VisionError("核对后正文为空或超过 50 万字符，请拆分")
            raw = await self.source(item)
            content_hash = digest([source, segments, str(document_date), document_type])
            doc = await session.scalar(
                select(Document).where(Document.content_hash == content_hash)
            )
            if doc and doc.status != "ready":
                raise VisionError("相同参考资料正在清理，请稍后重试", 409)
            path = None
            try:
                if doc is None:
                    identifier_doc = str(uuid.uuid4())
                    root = self.settings.upload_dir.resolve()
                    root.mkdir(parents=True, exist_ok=True)
                    path = root / (identifier_doc + Path(item.filename).suffix.lower())
                    with path.open("xb") as stream:
                        stream.write(raw)
                    chunker = DocumentChunker()
                    chunks = [
                        (number, chunk)
                        for number, value in segments
                        for chunk in chunker.chunk(value)
                    ]
                    doc = Document(
                        id=identifier_doc,
                        filename=item.filename,
                        mime_type=item.mime_type,
                        storage_path=str(path),
                        status="ready",
                        content_hash=content_hash,
                        index_status="not_indexed",
                        document_date=document_date,
                        document_type=document_type,
                        meta={
                            "vision_source": source,
                            "chunk_count": len(chunks),
                            "page_numbers_available": True,
                            "warning": "人工核对后的识别转录；仅含所选页。关键词检索不调用 Embedding API。",
                        },
                    )
                    session.add(doc)
                    for index, (number, chunk) in enumerate(chunks):
                        session.add(
                            DocumentChunk(
                                id=str(uuid.uuid4()),
                                document_id=identifier_doc,
                                chunk_index=index,
                                content=chunk,
                                meta={"page_number": number, "section": "人工核对的识别转录"},
                            )
                        )
                    await session.flush()
                if allow_embedding and doc.index_status != "ready":
                    await self.knowledge.enqueue(session, doc.id)
                state = deepcopy(item.state)
                key = "document:" + content_hash
                state["publications"] = [p for p in state["publications"] if p["key"] != key] + [
                    {"key": key, "kind": "document", "id": doc.id}
                ]
                for number in numbers:
                    state["pages"][str(number)]["reviewed"] = True
                item.state = state
                item.revision += 1
                # Commit here so a failed transaction also cleans its managed source copy.
                await session.commit()
                return {
                    "draft": self.describe(item),
                    "document_id": doc.id,
                    "embedding_requested": allow_embedding,
                }
            except BaseException:
                if path is not None:
                    path.unlink(missing_ok=True)
                raise
