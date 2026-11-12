"""复用已有 Conversation/Message 表，保存不可变报告和有版本的可信上下文。"""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.infrastructure.database.models import Conversation, Message


class HistoryUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("分析历史暂时不可用，请检查数据库后重试")


def conversation_id(session_id):
    # 兼容历史非 UUID 会话 ID，不将外部字符串拼接到 SQL。
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "analyst-session:" + session_id))


class AnalysisArchive:
    def __init__(self, engine, *, max_turns=10, max_payload_bytes=2_000_000):
        self.engine = engine
        self.max_turns = max_turns
        self.max_payload_bytes = max_payload_bytes

    @classmethod
    def from_settings(cls, settings):
        url = make_url(settings.database_url)
        if url.get_backend_name() != "postgresql":
            raise ValueError("持久化历史需要项目 PostgreSQL")
        engine = create_engine(
            url.set(drivername="postgresql+psycopg2"),
            hide_parameters=True,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
            pool_size=3,
            max_overflow=3,
        )
        return cls(engine, max_turns=settings.session_max_turns)

    async def _call(self, function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except SQLAlchemyError as exc:
            logger.warning("分析历史操作未完成: {}", type(exc).__name__)
            raise HistoryUnavailable() from None

    async def check_ready(self):
        def check():
            with Session(self.engine) as session:
                session.execute(select(Conversation.id).limit(0))
                session.execute(select(Message.id).limit(0))

        await self._call(check)

    async def aclose(self):
        await asyncio.to_thread(self.engine.dispose)

    async def context(self, session_id):
        def read():
            with Session(self.engine) as session:
                item = session.get(Conversation, conversation_id(session_id))
                meta = item.meta or {} if item else {}
                if (
                    not isinstance(meta, dict)
                    or not isinstance(meta.get("state", {}), dict)
                    or not isinstance(meta.get("recent", []), list)
                ):
                    raise HistoryUnavailable()
                recent = meta.get("recent", [])
                if len(recent) % 2 or any(
                    not isinstance(message, dict)
                    or message.get("role") != ("user" if index % 2 == 0 else "assistant")
                    or not isinstance(message.get("content"), str)
                    for index, message in enumerate(recent)
                ):
                    raise HistoryUnavailable()
                return {
                    "exists": item is not None,
                    "revision": meta.get("revision", 0),
                    "state": deepcopy(meta.get("state", {})),
                    "history": deepcopy(meta.get("recent", [])),
                }

        return await self._call(read)

    async def save(self, snapshot, *, trusted, state, revision):
        # JSON roundtrip makes a detached snapshot; later UI/response mutations cannot rewrite it.
        snapshot = json.loads(
            json.dumps(snapshot, ensure_ascii=False, allow_nan=False, default=str)
        )
        if len(json.dumps(snapshot, ensure_ascii=False).encode()) > self.max_payload_bytes:
            raise HistoryUnavailable()

        def write():
            identifier = conversation_id(snapshot["session_id"])
            with Session(self.engine) as session, session.begin():
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                inserting = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
                now = datetime.now(UTC)
                session.execute(
                    inserting(Conversation)
                    .values(
                        id=identifier,
                        title=snapshot["question"][:80],
                        meta={},
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["id"])
                )
                conversation = session.scalar(
                    select(Conversation).where(Conversation.id == identifier).with_for_update()
                )
                existing = session.get(Message, snapshot["trace_id"])
                if existing is not None:
                    return deepcopy(existing.meta["response"])
                meta = deepcopy(conversation.meta or {})
                applied = trusted and meta.get("revision", 0) == revision
                if applied:
                    meta["revision"] = revision + 1
                    meta["state"] = deepcopy(state)
                    meta["recent"] = [
                        *meta.get("recent", []),
                        {"role": "user", "content": snapshot["question"]},
                        {"role": "assistant", "content": snapshot["answer"]},
                    ][-self.max_turns * 2 :]
                elif trusted:
                    snapshot["analysis"].setdefault("warnings", []).append(
                        "本轮基于较早的会话条件完成，未更新后续上下文；请核对最新分析条件。"
                    )
                order = meta.get("turn_order", 0) + 1
                meta["turn_order"] = order
                meta["history_format"] = 1
                meta["session_id"] = snapshot["session_id"]
                conversation.meta, conversation.updated_at = meta, now
                snapshot.update(
                    archive_status="saved",
                    context_applied=applied,
                    created_at=now.isoformat(),
                    analysis_state=deepcopy(meta.get("state", {})),
                )
                session.add(
                    Message(
                        id=str(uuid.uuid5(uuid.UUID(snapshot["trace_id"]), "question")),
                        conversation_id=identifier,
                        role="user",
                        content=snapshot["question"],
                        meta={"turn_order": order},
                        created_at=now,
                    )
                )
                session.add(
                    Message(
                        id=snapshot["trace_id"],
                        conversation_id=identifier,
                        role="assistant",
                        content=snapshot["answer"],
                        meta={
                            "response": snapshot,
                            "turn_order": order,
                            "created_at": now.isoformat(),
                            "trusted_context": applied,
                        },
                        created_at=now,
                    )
                )
                return snapshot

        return await self._call(write)

    async def conversations(self, limit=30):
        def read():
            with Session(self.engine) as session:
                rows = session.scalars(
                    select(Conversation)
                    .where(Conversation.meta["history_format"].as_integer() == 1)
                    .order_by(Conversation.updated_at.desc(), Conversation.id)
                    .limit(limit)
                )
                return [
                    {
                        "session_id": (item.meta or {}).get("session_id", str(item.id)),
                        "title": item.title,
                        "updated_at": item.updated_at.isoformat(),
                        "turn_count": (item.meta or {}).get("turn_order", 0),
                    }
                    for item in rows
                ]

        return await self._call(read)

    async def reports(self, session_id, limit=50):
        def read():
            with Session(self.engine) as session:
                rows = session.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id(session_id),
                        Message.role == "assistant",
                    )
                    .order_by(Message.meta["turn_order"].as_integer().desc())
                    .limit(limit)
                )
                return [
                    deepcopy(item.meta["response"])
                    for item in reversed(list(rows))
                    if item.meta and "response" in item.meta
                ]

        return await self._call(read)

    async def report(self, report_id):
        def read():
            with Session(self.engine) as session:
                row = session.get(Message, report_id)
                return (
                    deepcopy((row.meta or {}).get("response"))
                    if row and row.role == "assistant"
                    else None
                )

        return await self._call(read)

    async def clear_context(self, session_id):
        def write():
            with Session(self.engine) as session, session.begin():
                row = session.scalar(
                    select(Conversation)
                    .where(Conversation.id == conversation_id(session_id))
                    .with_for_update()
                )
                if row is not None:
                    meta = deepcopy(row.meta or {})
                    meta.update(revision=meta.get("revision", 0) + 1, state={}, recent=[])
                    row.meta = meta

        await self._call(write)
