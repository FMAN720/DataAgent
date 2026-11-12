"""异步数据库引擎与会话工厂。"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.database.models import Document, DocumentChunk

# 默认异步 PostgreSQL（需安装 asyncpg）
_default_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_db"


def normalize_async_database_url(url: str) -> str:
    """将同步驱动 URL 转为 SQLAlchemy 异步 URL。"""
    if "+asyncpg" in url:
        return url
    u = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    u = u.replace("postgres://", "postgresql+asyncpg://")
    u = re.sub(
        r"^postgresql://",
        "postgresql+asyncpg://",
        u,
    )
    return u


def init_engine(database_url: str | None = None, **engine_kwargs: Any) -> AsyncEngine:
    """创建异步引擎（应用启动时调用一次）。"""
    url = normalize_async_database_url(database_url or _default_url)
    kwargs = {"echo": False, "pool_pre_ping": True, "hide_parameters": True}
    kwargs.update(engine_kwargs)
    engine = create_async_engine(url, **kwargs)
    logger.info("数据库引擎已初始化（已隐藏凭据）")
    return engine


_engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_session(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """绑定全局 session 工厂。"""
    global _engine, async_session_factory
    _engine = engine
    async_session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
    return async_session_factory


async def check_document_storage(engine: AsyncEngine) -> None:
    """检查连接与 agent schema；DDL 由 Alembic 迁移账号执行。"""
    async with engine.connect() as connection:
        await connection.execute(select(Document).limit(0))
        await connection.execute(select(DocumentChunk.id).limit(0))


async def get_async_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """依赖注入用：获取异步会话（由路由层负责 commit）。"""
    factory = getattr(request.app.state, "document_session_factory", None)
    if factory is None:
        reason = getattr(request.app.state, "document_error", None) or "文档服务未启用"
        raise HTTPException(status_code=503, detail=reason)
    async with factory() as session:
        yield session
