"""文档管理 API：上传与列表。"""

from __future__ import annotations

import hashlib
import importlib.util
import uuid
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.etl import ETLPipeline
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.session import get_async_session
from app.knowledge.service import document_conditions
from app.knowledge.types import DocumentType, EmbeddingError, KnowledgeError, SearchResponse
from app.models.schemas import DocumentInfo, DocumentUploadResponse

router = APIRouter(tags=["documents"])

_MIME_TYPES = {
    ".txt": {"text/plain"},
    ".md": {"text/plain", "text/markdown", "text/x-markdown"},
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}


def _upload_name(filename: str | None, content_type: str | None) -> tuple[str, str]:
    # 同时处理 Windows 和 POSIX 路径；实际磁盘文件名始终由服务端生成。
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)
    suffix = Path(name).suffix.lower()
    if suffix not in _MIME_TYPES:
        raise HTTPException(status_code=415, detail="当前仅支持 TXT、Markdown、PDF 和 DOCX 文件")
    if not name or len(name) > 255:
        raise HTTPException(status_code=422, detail="文件名为空或超过 255 个字符")
    mime = (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if mime != "application/octet-stream" and mime not in _MIME_TYPES[suffix]:
        raise HTTPException(status_code=415, detail="文件类型与扩展名不匹配")
    return name, suffix


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    parts: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(min(64 * 1024, max_bytes - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件超过大小上限（{max_bytes} 字节）")
        parts.append(chunk)
    return b"".join(parts)


def _validate_content(raw: bytes, suffix: str) -> None:
    if not raw:
        raise HTTPException(status_code=422, detail="文件内容为空")
    if suffix == ".docx":
        if not raw.startswith(b"PK\x03\x04"):
            raise HTTPException(status_code=422, detail="文件不是有效的 DOCX")
        return
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(status_code=422, detail="文件不是有效的 PDF")
        if importlib.util.find_spec("pypdf") is None:
            raise HTTPException(status_code=503, detail="PDF 解析依赖 pypdf 未安装")
        return
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="文本文件必须使用 UTF-8 编码") from exc
    if "\x00" in content or raw.startswith((b"%PDF-", b"PK\x03\x04")):
        raise HTTPException(status_code=422, detail="文本文件包含二进制内容")
    if not content.strip():
        raise HTTPException(status_code=422, detail="文件内容为空")


def _save_file(dest: Path, raw: bytes) -> None:
    created = False
    try:
        with dest.open("xb") as stream:
            created = True
            stream.write(raw)
    except BaseException:
        if created:
            dest.unlink(missing_ok=True)
        raise


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(..., description="上传的文件"),
    session: AsyncSession = Depends(get_async_session),
    settings: Settings = Depends(get_settings),
    # FastAPI 只对精确的 Request 类型注入请求；默认 None 便于测试直接调用
    request: Request = None,  # type: ignore[assignment]
    document_date: Annotated[date | None, Form()] = None,
    document_type: Annotated[DocumentType, Form()] = "reference",
) -> DocumentUploadResponse:
    """上传文档并执行 ETL 分块后写入数据库。"""
    try:
        safe_name, suffix = _upload_name(file.filename, file.content_type)
        raw = await _read_upload(file, settings.upload_max_bytes)
    finally:
        await file.close()
    _validate_content(raw, suffix)

    knowledge = getattr(request.app.state, "knowledge_service", None) if request else None
    content_hash = hashlib.sha256(raw).hexdigest()
    if knowledge:
        try:
            existing = await knowledge.duplicate(session, content_hash)
        except KnowledgeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if existing:
            return _duplicate_response(existing)

    pipeline = ETLPipeline()
    try:
        etl = await pipeline.run_bytes(
            raw,
            filename=safe_name,
            mime_type=file.content_type,
        )
    except Exception as exc:
        logger.exception("ETL 失败: {}", exc)
        raise HTTPException(status_code=422, detail="文档解析失败，请检查文件内容") from exc

    if not etl.parsed.text.strip() or not etl.chunks:
        raise HTTPException(status_code=422, detail="未提取到可用文本，文件不会入库")

    upload_root = settings.upload_dir.resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    doc_id = str(uuid.uuid4())
    dest = upload_root / f"{doc_id}{suffix}"
    # 写入是一个短小、受大小上限约束的本地操作，完成后才进入数据库事务。
    try:
        _save_file(dest, raw)
    except OSError as exc:
        logger.exception("保存上传文件失败")
        raise HTTPException(status_code=500, detail="保存上传文件失败") from exc

    doc = Document(
        id=doc_id,
        filename=safe_name,
        mime_type=file.content_type,
        storage_path=str(dest),
        status="ready",
        content_hash=content_hash,
        index_status="not_indexed",
        meta={**etl.parsed.meta, "chunk_count": len(etl.chunks)},
        document_date=document_date,
        document_type=document_type,
    )
    try:
        session.add(doc)
        for i, chunk_text in enumerate(etl.chunks):
            chunk = DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                chunk_index=i,
                content=chunk_text[:65000],
                vector_id=None,
                meta=etl.chunk_metadata[i] if i < len(etl.chunk_metadata) else None,
            )
            session.add(chunk)
        if knowledge and knowledge.embedding_enabled:
            await session.flush()
            await knowledge.enqueue(session, doc_id)
        await session.commit()
    except BaseException as exc:
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("文档事务回滚失败")
        finally:
            dest.unlink(missing_ok=True)
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, IntegrityError) and knowledge:
            existing = await knowledge.duplicate(session, content_hash)
            if existing:
                return _duplicate_response(existing)
        logger.exception("文档入库失败，已回滚并清理文件")
        raise HTTPException(status_code=503, detail="文档存储失败，未完成上传") from exc

    return DocumentUploadResponse(
        document_id=doc_id,
        filename=safe_name,
        status="ready",
        chunk_count=len(etl.chunks),
        message="上传并分块成功",
        index_status=doc.index_status,
    )


@router.get("/documents", response_model=list[DocumentInfo])
async def list_documents(
    session: AsyncSession = Depends(get_async_session),
    date_from: date | None = None,
    date_to: date | None = None,
    document_type: DocumentType | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[DocumentInfo]:
    """列出已入库文档元数据。"""
    try:
        conditions = document_conditions(
            date_from=date_from, date_to=date_to, document_type=document_type
        )
        result = await session.execute(
            select(Document).where(*conditions).order_by(Document.created_at.desc()).limit(limit)
        )
        rows = result.scalars().all()
        out: list[DocumentInfo] = []
        for d in rows:
            out.append(
                DocumentInfo(
                    id=d.id,
                    filename=d.filename,
                    mime_type=d.mime_type,
                    status=d.status,
                    created_at=d.created_at.isoformat() if d.created_at else None,
                    index_status=d.index_status,
                    index_error=d.index_error,
                    document_date=d.document_date.isoformat() if d.document_date else None,
                    document_type=d.document_type,
                    chunk_count=(d.meta or {}).get("chunk_count", 0),
                )
            )
        return out
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        logger.exception("查询文档列表失败: {}", exc)
        raise HTTPException(status_code=503, detail="文档存储暂时不可用") from exc


def _duplicate_response(doc: Document) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        document_id=doc.id,
        filename=doc.filename,
        status=doc.status,
        chunk_count=(doc.meta or {}).get("chunk_count", 0),
        index_status=doc.index_status,
        duplicate=True,
        message="相同内容已存在，复用现有文档",
    )


def _knowledge(request: Request):
    service = getattr(request.app.state, "knowledge_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="文档检索未就绪，请检查数据库迁移")
    return service


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["keyword", "vector", "hybrid"] = "hybrid"
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    top_k: int | None = Field(default=None, ge=1, le=10)
    date_from: date | None = None
    date_to: date | None = None
    document_type: DocumentType | None = None


@router.post("/documents/search", response_model=SearchResponse)
async def search_documents(body: DocumentSearchRequest, request: Request):
    service = _knowledge(request)
    try:
        return await service.search(
            body.query,
            mode=body.mode,
            top_k=body.top_k,
            document_ids=None
            if body.document_ids is None
            else [str(value) for value in body.document_ids],
            date_from=body.date_from,
            date_to=body.date_to,
            document_type=body.document_type,
        )
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/documents/{document_id}")
async def document_details(
    document_id: uuid.UUID,
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=20),
    chunk_id: uuid.UUID | None = None,
):
    try:
        return await _knowledge(request).details(
            str(document_id),
            offset=offset,
            limit=limit,
            chunk_id=str(chunk_id) if chunk_id else None,
        )
    except KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


class DocumentMetadataRequest(BaseModel):
    document_date: date | None = None
    document_type: DocumentType = "reference"


@router.patch("/documents/{document_id}")
async def update_document_metadata(
    document_id: uuid.UUID, body: DocumentMetadataRequest, request: Request
):
    backend = _knowledge(request)
    async with backend.factory() as session, session.begin():
        doc = await session.scalar(
            select(Document).where(Document.id == str(document_id)).with_for_update()
        )
        if not doc or doc.status != "ready":
            raise HTTPException(status_code=404, detail="文档不存在或正在删除")
        for key in body.model_fields_set:
            setattr(doc, key, getattr(body, key))
    return {
        "id": str(document_id),
        "document_date": doc.document_date.isoformat() if doc.document_date else None,
        "document_type": doc.document_type,
    }


@router.get("/documents/{document_id}/index")
async def index_status(document_id: uuid.UUID, request: Request):
    try:
        return await _knowledge(request).index_state(str(document_id))
    except KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/documents/{document_id}/index", status_code=202)
async def enqueue_index(document_id: uuid.UUID, request: Request, force: bool = False):
    service = _knowledge(request)
    try:
        async with service.factory() as session, session.begin():
            job = await service.enqueue(session, str(document_id), force=force)
            return {"job_id": job.id, "document_id": str(document_id), "status": job.status}
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.delete("/documents/{document_id}")
async def delete_document(document_id: uuid.UUID, request: Request, response: Response):
    done = await _knowledge(request).delete_document(str(document_id))
    if not done:
        response.status_code = 202
    return {"document_id": str(document_id), "status": "deleted" if done else "cleanup_pending"}


@router.post("/documents/{document_id}/retry", status_code=202)
async def retry_index(document_id: uuid.UUID, request: Request):
    service = _knowledge(request)
    try:
        async with service.factory() as session, session.begin():
            job = await service.enqueue(session, str(document_id), retry=True)
            return {"job_id": job.id, "document_id": str(document_id), "status": job.status}
    except EmbeddingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except KnowledgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
