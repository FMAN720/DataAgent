"""分析历史与快照导出；读取不触发 SQL 重跑或模型调用。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.analysis.export import export_report

router = APIRouter(tags=["analysis"])


def _archive(request):
    service = getattr(request.app.state, "analysis_archive", None)
    if service is None:
        raise HTTPException(503, "分析历史未就绪，请检查 PostgreSQL 配置")
    return service


@router.get("/conversations")
async def conversations(request: Request, limit: int = Query(30, ge=1, le=50)):
    return await _archive(request).conversations(limit)


@router.get("/conversations/{session_id}")
async def conversation(session_id: str, request: Request, limit: int = Query(50, ge=1, le=100)):
    archive = _archive(request)
    context = await archive.context(session_id)
    if not context["exists"]:
        raise HTTPException(404, "会话不存在或尚无已完成分析")
    return {
        "session_id": session_id,
        "analysis_state": context["state"],
        "revision": context["revision"],
        "reports": await archive.reports(session_id, limit),
    }


@router.get("/analyses/{report_id}")
async def report(report_id: UUID, request: Request):
    value = await _archive(request).report(str(report_id))
    if value is None:
        raise HTTPException(404, "分析记录不存在")
    return value


def _examples(request):
    store = getattr(request.app.state, "sql_examples", None)
    if store is None:
        raise HTTPException(503, "SQL 示例库未就绪，请执行数据库迁移 0006_sql_examples")
    return store


class ExampleRequest(BaseModel):
    note: str = Field(default="", max_length=500)


@router.post("/analyses/{report_id}/sql-example", status_code=201)
async def save_sql_example(report_id: UUID, body: ExampleRequest, request: Request):
    """用户确认这份报告的 SQL 正确，存为同类问题的参考示例；不调用模型。"""
    from app.analysis.examples import SqlExampleError

    store = _examples(request)
    value = await report(report_id, request)
    try:
        return await store.add_from_report(value, body.note)
    except SqlExampleError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/sql-examples")
async def list_sql_examples(request: Request, limit: int = Query(50, ge=1, le=200)):
    return await _examples(request).recent(limit)


@router.delete("/sql-examples/{example_id}", status_code=204)
async def delete_sql_example(example_id: UUID, request: Request):
    if not await _examples(request).delete(str(example_id)):
        raise HTTPException(404, "示例不存在")


@router.get("/analyses/{report_id}/export")
async def export(
    report_id: UUID,
    request: Request,
    format: Literal["csv", "xlsx", "md"] = "md",
    table_id: str | None = Query(None, max_length=80),
):
    value = await report(report_id, request)
    try:
        import asyncio

        content, media_type = await asyncio.to_thread(export_report, value, format, table_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="analysis-{report_id}.{format}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
