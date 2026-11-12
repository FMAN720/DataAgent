"""Two-step file imports: review preview, then explicitly enqueue import."""

import uuid
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.datasets.parser import DatasetError, ExcelDependencyMissing, clean_filename

router = APIRouter(tags=["datasets"])


def service(request):
    result = getattr(request.app.state, "dataset_service", None)
    if result is None:
        raise HTTPException(status_code=503, detail="表格导入未就绪，请检查导入账号与数据库迁移")
    return result


class ImportRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    type_overrides: dict[
        str, Literal["text", "integer", "decimal", "boolean", "date", "datetime", "datetime_tz"]
    ] = Field(default_factory=dict)


@router.post("/datasets/preview")
async def preview(
    request: Request,
    file: UploadFile = File(...),
    encoding: str = Form("utf-8-sig"),
    delimiter: str = Form(","),
    sheet_name: str | None = Form(None),
):
    backend = service(request)
    try:
        try:
            filename = clean_filename(file.filename or "")
        except DatasetError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from None
        if filename.lower().endswith(".xlsx"):
            allowed_mimes = {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/octet-stream",
                "application/zip",
            }
        else:
            allowed_mimes = {
                "text/csv",
                "text/plain",
                "application/csv",
                "application/vnd.ms-excel",
                "application/octet-stream",
            }
        mime = (file.content_type or "application/octet-stream").split(";", 1)[0].lower()
        if mime not in allowed_mimes:
            raise HTTPException(status_code=415, detail="文件类型与扩展名不匹配")
        parts, size = [], 0
        while block := await file.read(min(65536, backend.settings.import_max_bytes - size + 1)):
            size += len(block)
            if size > backend.settings.import_max_bytes:
                raise HTTPException(status_code=413, detail="导入文件超过大小限制")
            parts.append(block)
        return await backend.stage(
            b"".join(parts), filename, encoding=encoding, delimiter=delimiter, sheet_name=sheet_name
        )
    except ExcelDependencyMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    finally:
        await file.close()


@router.get("/datasets")
async def list_datasets(request: Request, ready_only: bool = False):
    return await service(request).list(ready_only=ready_only)


@router.get("/datasets/{dataset_id}")
async def dataset_status(dataset_id: uuid.UUID, request: Request):
    try:
        return await service(request).get(str(dataset_id))
    except DatasetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/datasets/{dataset_id}/import", status_code=202)
async def enqueue_import(dataset_id: uuid.UUID, body: ImportRequest, request: Request):
    try:
        return await service(request).confirm(
            str(dataset_id), display_name=body.display_name, type_overrides=body.type_overrides
        )
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/datasets/{dataset_id}/retry", status_code=202)
async def retry_import(dataset_id: uuid.UUID, request: Request):
    try:
        return await service(request).confirm(str(dataset_id), retry=True)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: uuid.UUID, request: Request, response: Response):
    try:
        done = await service(request).delete(str(dataset_id))
    except DatasetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not done:
        response.status_code = 202
    return {"id": str(dataset_id), "status": "deleted" if done else "cleanup_pending"}
