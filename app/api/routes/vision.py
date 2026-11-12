"""Upload stays local. Recognition and reviewed publication are separate actions."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.types import DocumentType
from app.vision.types import PageResult, VisionError

router = APIRouter(tags=["vision"])


def service(request):
    result = getattr(request.app.state, "vision_service", None)
    if result is None:
        raise VisionError("图片识别草稿服务未就绪，请检查数据库迁移与解析库", 503)
    return result


class Versioned(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


class RecognitionRequest(Versioned):
    pages: list[int] = Field(min_length=1, max_length=10)
    consent: bool = False


class EditRequest(Versioned):
    result: PageResult
    manual: bool = False


class TableRequest(Versioned):
    page_number: int = Field(ge=1)
    table_index: int = Field(ge=0)
    reviewed: bool = False


class DocumentRequest(Versioned):
    pages: list[int] = Field(min_length=1, max_length=10)
    reviewed: bool = False
    document_date: date | None = Field(default=None, strict=False)
    document_type: DocumentType = "reference"
    allow_embedding: bool = False


@router.post("/vision/drafts/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    backend = service(request)
    parts = []
    size = 0
    try:
        while block := await file.read(min(65536, backend.settings.vision_max_bytes - size + 1)):
            size += len(block)
            if size > backend.settings.vision_max_bytes:
                raise VisionError("文件超过图片/扫描件大小上限", 413)
            parts.append(block)
        return await backend.stage(b"".join(parts), file.filename, file.content_type)
    finally:
        await file.close()


@router.get("/vision/drafts")
async def drafts(request: Request):
    return await service(request).list()


@router.get("/vision/drafts/{identifier}")
async def draft(identifier: UUID, request: Request):
    return await service(request).get(str(identifier))


@router.get("/vision/drafts/{identifier}/pages/{number}/image")
async def image(identifier: UUID, number: int, request: Request):
    return Response(
        await service(request).image(str(identifier), number),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/vision/drafts/{identifier}/recognize")
async def recognize(identifier: UUID, body: RecognitionRequest, request: Request):
    return await service(request).recognize(
        str(identifier), body.revision, body.pages, body.consent
    )


@router.put("/vision/drafts/{identifier}/pages/{number}")
async def edit(identifier: UUID, number: int, body: EditRequest, request: Request):
    return await service(request).edit(
        str(identifier), body.revision, number, body.result.model_dump(), manual=body.manual
    )


@router.post("/vision/drafts/{identifier}/table")
async def table(identifier: UUID, body: TableRequest, request: Request):
    return await service(request).table(
        str(identifier), body.revision, body.page_number, body.table_index, body.reviewed
    )


@router.post("/vision/drafts/{identifier}/document")
async def document(identifier: UUID, body: DocumentRequest, request: Request):
    return await service(request).document(
        str(identifier),
        body.revision,
        body.pages,
        body.reviewed,
        document_date=body.document_date,
        document_type=body.document_type,
        allow_embedding=body.allow_embedding,
    )
