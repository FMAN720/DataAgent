"""健康检查：进程存活与依赖探测。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from loguru import logger

from app.config import get_settings
from app.infrastructure.database.session import check_document_storage

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """轻量存活探针（不访问外部依赖）。"""
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """核心问答不可用返回 503；可选文档库不可用时保留查数能力。"""
    settings = get_settings()
    agent = getattr(request.app.state, "agent", None)
    analysis_ok = False
    session_ok = False
    if agent is not None:
        try:
            await asyncio.wait_for(agent.check_ready(), timeout=3.0)
            analysis_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("分析库就绪检查失败: {}", type(exc).__name__)
        try:
            await asyncio.wait_for(agent.check_session_ready(), timeout=3.0)
            session_ok = True
        except Exception as exc:
            logger.warning("会话存储就绪检查失败: {}", type(exc).__name__)

    document_engine = getattr(request.app.state, "document_engine", None)
    document_ok = False
    if document_engine is not None:
        try:
            await asyncio.wait_for(check_document_storage(document_engine), timeout=3.0)
            document_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("文档库就绪检查失败: {}", type(exc).__name__)

    archive = getattr(request.app.state, "analysis_archive", None)
    history_ok = True
    if archive is not None:
        try:
            await asyncio.wait_for(archive.check_ready(), timeout=3)
        except Exception:
            history_ok = False
    core_ok = agent is not None and analysis_ok and session_ok and history_ok
    if not core_ok:
        response.status_code = 503

    return {
        "status": "unavailable" if not core_ok else "ready" if document_ok else "degraded",
        "agent": "up" if agent is not None else "down",
        "analysis_database": "up" if analysis_ok else "down",
        "session_store": "up" if session_ok else "down",
        "session_backend": getattr(agent, "session_backend", settings.session_backend),
        "analysis_history": "up"
        if archive is not None and history_ok
        else "down"
        if archive is not None
        else "disabled",
        "document_database": "up" if document_ok else "down",
        "database": "up" if analysis_ok else "down",
        "app_env": settings.app_env,
        "document_retrieval": "up"
        if getattr(request.app.state, "knowledge_service", None)
        else "down",
        "dataset_imports": "up" if getattr(request.app.state, "dataset_service", None) else "down",
        "embedding": "disabled"
        if not settings.embedding_enabled
        else "configured"
        if (
            getattr(request.app.state, "knowledge_service", None)
            and request.app.state.knowledge_service.embedder
        )
        else "unavailable",
    }
