"""Staging metadata uses the app role; uploads DDL uses the separate importer role."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path

from loguru import logger
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    MetaData,
    Numeric,
    Table,
    Text,
    and_,
    inspect,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.config import Settings
from app.datasets.parser import DatasetError, canonical_digest, clean_filename, parse_table
from app.infrastructure.database.models import UploadedDataset, _utcnow


class ImportLeaseLost(Exception):
    pass


def dataset_table_name(dataset_id):
    return "dataset_" + uuid.UUID(dataset_id).hex


def table_definition(dataset_id, columns, *, marker=None):
    types = {
        "text": Text(),
        "integer": BigInteger(),
        "decimal": Numeric(),
        "boolean": Boolean(),
        "date": Date(),
        "datetime": DateTime(timezone=False),
        "datetime_tz": DateTime(timezone=True),
    }
    names = [column["name"] for column in columns]
    if len(names) != len(set(names)) or "_row_number" in names:
        raise DatasetError("导入字段定义冲突")
    return Table(
        dataset_table_name(dataset_id),
        MetaData(),
        Column("_row_number", BigInteger, primary_key=True, autoincrement=False),
        *[
            Column(
                column["name"],
                types[column["type"]],
                nullable=column["nullable"],
                comment="源字段：" + column["label"] + "；保留源文件单位",
            )
            for column in columns
        ],
        schema="uploads",
        comment=json.dumps(marker, ensure_ascii=False, separators=(",", ":")) if marker else None,
    )


async def advisory_lock(connection, dataset_id):
    key = int.from_bytes(uuid.UUID(dataset_id).bytes[:8], "big", signed=True)
    await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


class DatasetService:
    def __init__(self, factory, import_engine, settings: Settings, refresh_schema=None):
        self.factory, self.import_engine, self.settings = factory, import_engine, settings
        self.refresh_schema = refresh_schema
        self._parse_slots = asyncio.Semaphore(2)

    async def check_schema(self):
        async with self.factory() as session:
            await session.execute(select(UploadedDataset).limit(0))
        async with self.import_engine.connect() as connection:
            allowed = await connection.scalar(
                text("SELECT has_schema_privilege(current_user, 'uploads', 'CREATE')")
            )
            if not allowed:
                raise DatasetError("导入账号缺少 uploads 建表权限")
            excessive = await connection.scalar(
                text(
                    "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
            # OID overload avoids resolving a qualified table in a schema the importer
            # correctly cannot enter. Check every existing non-upload application table.
            outside = await connection.scalar(
                text("""SELECT
                has_schema_privilege(current_user,'business','CREATE') OR
                has_schema_privilege(current_user,'agent','CREATE') OR
                has_schema_privilege(current_user,'public','CREATE') OR EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname IN ('business','agent','public') AND c.relkind IN ('r','p')
                    AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')
                )""")
            )
            if excessive or outside:
                raise DatasetError("导入连接权限过大，请使用仅有 uploads 写权限的账号")

    async def parse(self, raw, filename, **options):
        async with self._parse_slots:
            return await asyncio.to_thread(parse_table, raw, filename, self.settings, **options)

    def managed_path(self, dataset):
        path = Path(dataset.storage_path)
        if (
            path.resolve().parent != self.settings.import_dir.resolve()
            or path.is_symlink()
            or path.stem != dataset.id
            or path.suffix not in (".csv", ".xlsx")
        ):
            raise DatasetError("导入文件路径不受本项目管理")
        return path

    async def source(self, dataset):
        path = self.managed_path(dataset)
        try:

            def read():
                with path.open("rb") as stream:
                    return stream.read(self.settings.import_max_bytes + 1)

            raw = await asyncio.to_thread(read)
        except OSError:
            raise DatasetError("源文件不可读取") from None
        if (
            len(raw) > self.settings.import_max_bytes
            or hashlib.sha256(raw).hexdigest() != dataset.content_hash
        ):
            raise DatasetError("源文件与预览不一致，请重新上传")
        return raw

    async def stage(
        self,
        raw,
        filename,
        *,
        encoding="utf-8-sig",
        delimiter=",",
        sheet_name=None,
        provenance=None,
    ):
        filename = clean_filename(filename)
        parsed = await self.parse(
            raw, filename, encoding=encoding, delimiter=delimiter, sheet_name=sheet_name
        )
        content_hash = hashlib.sha256(raw).hexdigest()
        options = {
            "encoding": encoding,
            "delimiter": delimiter,
            "sheet_name": parsed.selected_sheet,
            "sheet_names": parsed.sheet_names,
            "parser_version": 1,
        }
        if provenance is not None:
            options["vision_source"] = provenance
        fingerprint = hashlib.sha256(
            json.dumps([content_hash, options], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        async with self.factory() as session:
            existing = await session.scalar(
                select(UploadedDataset).where(UploadedDataset.fingerprint == fingerprint)
            )
            if existing:
                if existing.status == "deleting":
                    raise DatasetError("相同数据集正在删除，请稍后重试")
                return self.describe(existing, duplicate=True)
        dataset_id = str(uuid.uuid4())
        root = self.settings.import_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / (dataset_id + Path(filename).suffix.lower())

        def save():
            with path.open("xb") as stream:
                stream.write(raw)

        try:
            save()  # bounded local write; cancellation cannot strand an untracked save thread
            async with self.factory() as session:
                dataset = UploadedDataset(
                    id=dataset_id,
                    filename=filename,
                    storage_path=str(path),
                    content_hash=content_hash,
                    fingerprint=fingerprint,
                    table_name=dataset_table_name(dataset_id),
                    display_name=Path(filename).stem[:128],
                    options=options,
                    columns=parsed.columns,
                    preview_rows=parsed.preview,
                    warnings=parsed.warnings,
                    row_count=len(parsed.rows),
                    imported_rows=0,
                    status="previewed",
                    attempts=0,
                )
                session.add(dataset)
                await session.commit()
                return self.describe(dataset)
        except IntegrityError:
            path.unlink(missing_ok=True)
            async with self.factory() as session:
                existing = await session.scalar(
                    select(UploadedDataset).where(UploadedDataset.fingerprint == fingerprint)
                )
                if existing and existing.status != "deleting":
                    return self.describe(existing, duplicate=True)
            raise
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def describe(dataset, *, duplicate=False, include_preview=True):
        value = {
            "id": dataset.id,
            "filename": dataset.filename,
            "display_name": dataset.display_name,
            "status": dataset.status,
            "duplicate": duplicate,
            "content_hash": dataset.content_hash,
            "table": "uploads." + dataset.table_name,
            "columns": dataset.columns,
            "warnings": dataset.warnings,
            "row_count": dataset.row_count,
            "imported_rows": dataset.imported_rows,
            "attempts": dataset.attempts,
            "error": dataset.error,
            "options": dataset.options,
            "created_at": dataset.created_at.isoformat() if dataset.created_at else None,
        }
        if dataset.options.get("vision_source"):
            value["warnings"] = [
                *value["warnings"],
                "此表来自图片/扫描件识别，用户已确认转录；仍需核对单位、口径和完整性，原始来源见图片草稿。",
            ]
        if include_preview:
            value["preview_rows"] = dataset.preview_rows
        return value

    async def get(self, dataset_id):
        async with self.factory() as session:
            dataset = await session.get(UploadedDataset, dataset_id)
            if not dataset:
                raise DatasetError("数据集不存在")
            return self.describe(dataset)

    async def list(self, *, ready_only=False, query="", limit=50):
        async with self.factory() as session:
            statement = select(UploadedDataset).options(defer(UploadedDataset.preview_rows))
            if ready_only:
                statement = statement.where(UploadedDataset.status == "ready")
            if query:
                statement = statement.where(
                    or_(
                        UploadedDataset.filename.contains(query, autoescape=True),
                        UploadedDataset.display_name.contains(query, autoescape=True),
                    )
                )
            items = list(
                await session.scalars(
                    statement.order_by(UploadedDataset.created_at.desc()).limit(limit)
                )
            )
            return [self.describe(item, include_preview=False) for item in items]

    async def confirm(self, dataset_id, *, display_name=None, type_overrides=None, retry=False):
        async with self.factory() as session, session.begin():
            dataset = await session.scalar(
                select(UploadedDataset).where(UploadedDataset.id == dataset_id).with_for_update()
            )
            if dataset is None or dataset.status == "deleting":
                raise DatasetError("数据集不存在或正在删除")
            if dataset.status in ("queued", "importing", "ready"):
                return self.describe(dataset)
            if dataset.status == "failed" and not retry:
                return self.describe(dataset)
            raw = await self.source(dataset)
            options = {
                k: v
                for k, v in dataset.options.items()
                if k in ("encoding", "delimiter", "sheet_name")
            }
            if retry:
                # A failed job's schema is immutable; retry only the same reviewed import.
                if type_overrides:
                    raise DatasetError("重试时不能修改字段类型，请重新上传")
                type_overrides = {column["name"]: column["type"] for column in dataset.columns}
            parsed = await self.parse(
                raw, dataset.filename, **options, type_overrides=type_overrides
            )
            if display_name is not None:
                if not display_name.strip() or len(display_name) > 128:
                    raise DatasetError("显示名称需要 1 到 128 个字符")
                dataset.display_name = display_name.strip()
            dataset.columns, dataset.preview_rows, dataset.warnings = (
                parsed.columns,
                parsed.preview,
                parsed.warnings,
            )
            dataset.status, dataset.error, dataset.attempts, dataset.imported_rows = (
                "queued",
                None,
                0,
                0,
            )
            dataset.available_at, dataset.lease_token, dataset.locked_until = _utcnow(), None, None
            await session.flush()
            return self.describe(dataset)

    async def notify_schema(self):
        if self.refresh_schema:
            try:
                await self.refresh_schema()
            except Exception as exc:
                logger.warning("导入已提交，等待表结构缓存刷新: {}", type(exc).__name__)

    async def delete(self, dataset_id):
        async with self.factory() as session, session.begin():
            dataset = await session.scalar(
                select(UploadedDataset).where(UploadedDataset.id == dataset_id).with_for_update()
            )
            if not dataset:
                return True
            dataset.status = "deleting"
            dataset.lease_token, dataset.locked_until = None, None
        return await self.finish_delete(dataset_id)

    async def finish_delete(self, dataset_id):
        async with self.factory() as session:
            dataset = await session.get(UploadedDataset, dataset_id)
            if not dataset or dataset.status != "deleting":
                return True
            if dataset.table_name != dataset_table_name(dataset_id):
                raise DatasetError("导入表名不匹配")
            path = self.managed_path(dataset)
        # Never hold a metadata row lock while waiting for the importer advisory lock.
        async with self.import_engine.begin() as connection:
            await advisory_lock(connection, dataset_id)
            exists = await connection.run_sync(
                lambda c: inspect(c).has_table(dataset.table_name, schema="uploads")
            )
            if exists:
                comment = await connection.run_sync(
                    lambda c: inspect(c).get_table_comment(dataset.table_name, schema="uploads")[
                        "text"
                    ]
                )
                try:
                    marker = json.loads(comment)
                except (TypeError, ValueError):
                    raise DatasetError("目标表不是本项目生成的导入表") from None
                if (
                    marker.get("dataset_id") != dataset_id
                    or marker.get("content_hash") != dataset.content_hash
                ):
                    raise DatasetError("目标表来源标记不匹配，已拒绝删除")
                table = table_definition(dataset_id, dataset.columns)
                await connection.run_sync(table.drop)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            async with self.factory() as session, session.begin():
                await session.execute(
                    update(UploadedDataset)
                    .where(UploadedDataset.id == dataset_id, UploadedDataset.status == "deleting")
                    .values(error="import_file_cleanup_pending")
                )
            return False
        async with self.factory() as session, session.begin():
            current = await session.scalar(
                select(UploadedDataset).where(UploadedDataset.id == dataset_id).with_for_update()
            )
            if current and current.status == "deleting":
                await session.delete(current)
        await self.notify_schema()
        return True

    async def cleanup_deletions(self):
        async with self.factory() as session:
            ids = list(
                await session.scalars(
                    select(UploadedDataset.id).where(UploadedDataset.status == "deleting").limit(20)
                )
            )
        for dataset_id in ids:
            try:
                await self.finish_delete(dataset_id)
            except Exception as exc:
                logger.warning("数据集清理等待重试: {}", type(exc).__name__)


class DatasetWorker:
    def __init__(self, service: DatasetService, *, dataset_ids=None):
        self.service, self.factory, self.settings = service, service.factory, service.settings
        self.dataset_ids = dataset_ids
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self.loop(), name="dataset-import-worker")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, 5)
            except (asyncio.CancelledError, TimeoutError):
                pass
            except Exception as exc:
                logger.warning("导入工作者关闭: {}", type(exc).__name__)
            self._task = None

    async def loop(self):
        while True:
            try:
                await self.service.cleanup_deletions()
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("导入任务等待重试: {}", type(exc).__name__)
            await asyncio.sleep(self.settings.import_poll_seconds)

    async def claim(self):
        now = _utcnow()
        async with self.factory() as session, session.begin():
            statement = (
                select(UploadedDataset)
                .where(
                    or_(
                        and_(
                            UploadedDataset.status == "queued", UploadedDataset.available_at <= now
                        ),
                        and_(
                            UploadedDataset.status == "importing",
                            UploadedDataset.locked_until <= now,
                        ),
                    )
                )
                .order_by(UploadedDataset.available_at, UploadedDataset.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if self.dataset_ids is not None:
                statement = statement.where(UploadedDataset.id.in_(self.dataset_ids))
            dataset = await session.scalar(statement)
            if dataset is None:
                return None
            dataset.status, dataset.lease_token = "importing", str(uuid.uuid4())
            dataset.locked_until = now + timedelta(seconds=self.settings.import_lease_seconds)
            dataset.attempts += 1
            await session.flush()
            return dataset.id, dataset.lease_token

    async def checkpoint(self, claim, *, progress=None, error=None, ready=False, retry=False):
        async with self.factory() as session, session.begin():
            dataset = await session.scalar(
                select(UploadedDataset).where(UploadedDataset.id == claim[0]).with_for_update()
            )
            if dataset is None or dataset.status != "importing" or dataset.lease_token != claim[1]:
                raise ImportLeaseLost()
            if progress is not None:
                dataset.imported_rows = progress
            dataset.locked_until = _utcnow() + timedelta(seconds=self.settings.import_lease_seconds)
            if ready or error:
                dataset.status = (
                    "ready"
                    if ready
                    else "queued"
                    if retry and dataset.attempts < self.settings.import_max_attempts
                    else "failed"
                )
                dataset.error, dataset.lease_token, dataset.locked_until = error, None, None
                if error:
                    dataset.imported_rows = 0
                    dataset.available_at = _utcnow() + timedelta(seconds=2)

    async def heartbeat(self, claim):
        while True:
            await asyncio.sleep(self.settings.import_lease_seconds / 3)
            async with self.factory() as session, session.begin():
                result = await session.execute(
                    update(UploadedDataset)
                    .where(
                        UploadedDataset.id == claim[0],
                        UploadedDataset.lease_token == claim[1],
                        UploadedDataset.status == "importing",
                    )
                    .values(
                        locked_until=_utcnow()
                        + timedelta(seconds=self.settings.import_lease_seconds)
                    )
                )
                if result.rowcount != 1:
                    return

    async def run_once(self):
        claim = await self.claim()
        if claim is None:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(claim))
        try:
            await self.process(claim)
        except ImportLeaseLost:
            pass
        except asyncio.CancelledError:
            try:
                await self.checkpoint(claim, error="import_interrupted", retry=True)
            except Exception as exc:  # noqa: BLE001 - 取消时尽力记录；租约到期后任务仍会被重新领取
                logger.warning("导入取消时未能保存进度: {}", type(exc).__name__)
            raise
        except Exception as exc:
            code = str(exc) if isinstance(exc, DatasetError) else "import_database_error"
            try:
                await self.checkpoint(claim, error=code, retry=not isinstance(exc, DatasetError))
            except ImportLeaseLost:
                pass
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def process(self, claim):
        async with self.factory() as session:
            dataset = await session.get(UploadedDataset, claim[0])
            if not dataset or dataset.status != "importing" or dataset.lease_token != claim[1]:
                raise ImportLeaseLost()
            if dataset.attempts > self.settings.import_max_attempts:
                raise DatasetError("import_attempts_exhausted")
            raw = await self.service.source(dataset)
            options = {
                k: v
                for k, v in dataset.options.items()
                if k in ("encoding", "delimiter", "sheet_name")
            }
            parsed = await self.service.parse(
                raw,
                dataset.filename,
                **options,
                type_overrides={column["name"]: column["type"] for column in dataset.columns},
            )
            if parsed.columns != dataset.columns or len(parsed.rows) != dataset.row_count:
                raise DatasetError("预览字段或行数发生变化，请重新上传")
            expected_rows = [(number, *values) for number, values in parsed.rows]
            digest = canonical_digest(expected_rows)
            marker = {
                "dataset_id": dataset.id,
                "content_hash": dataset.content_hash,
                "row_digest": digest,
                "column_digest": hashlib.sha256(
                    json.dumps(parsed.columns, sort_keys=True).encode()
                ).hexdigest(),
                "display_name": dataset.display_name,
                "filename": dataset.filename,
                "sheet": parsed.selected_sheet,
                "format_version": 1,
            }
            table = table_definition(dataset.id, parsed.columns, marker=marker)
        async with self.service.import_engine.begin() as connection:
            await advisory_lock(connection, claim[0])
            exists = await connection.run_sync(
                lambda c: inspect(c).has_table(table.name, schema="uploads")
            )
            if exists:
                comment = await connection.run_sync(
                    lambda c: inspect(c).get_table_comment(table.name, schema="uploads")["text"]
                )
                try:
                    stored_marker = json.loads(comment)
                except (ValueError, TypeError):
                    raise DatasetError("目标表缺少来源标记，已拒绝覆盖") from None
                if stored_marker != marker:
                    raise DatasetError("目标表来源或结构不一致，已拒绝覆盖")
                actual = await connection.run_sync(
                    lambda c: inspect(c).get_columns(table.name, schema="uploads")
                )
                signature = [
                    (col["name"], col["type"].compile(dialect=connection.dialect), col["nullable"])
                    for col in actual
                ]
                expected = [
                    (col.name, col.type.compile(dialect=connection.dialect), col.nullable)
                    for col in table.columns
                ]
                if signature != expected:
                    raise DatasetError("目标表字段被修改，已拒绝继续写入")
            else:
                await connection.run_sync(table.create)
                for start in range(0, len(parsed.rows), self.settings.import_batch_size):
                    batch = parsed.rows[start : start + self.settings.import_batch_size]
                    payload = [
                        {
                            "_row_number": number,
                            **dict(zip([c["name"] for c in parsed.columns], values)),
                        }
                        for number, values in batch
                    ]
                    await connection.execute(table.insert(), payload)
                    await self.checkpoint(claim, progress=start + len(batch))
            result = await connection.execute(select(table).order_by(table.c._row_number))
            if canonical_digest(result) != digest:
                raise DatasetError("导入数据校验不一致，已回滚")
            await self.checkpoint(claim, progress=len(parsed.rows))
        # If this metadata commit fails, next attempt verifies the existing table/digest.
        await self.checkpoint(claim, progress=len(parsed.rows), ready=True)
        await self.service.notify_schema()
