"""用户确认正确的「问题 → SQL」示例库。

只有用户在已保存的报告上明确确认后才写入；示例只作写法参考，Agent 仍须重新查询，
不能把示例里的数字当作本轮证据。召回用问题文本的 BM25，不调用 Embedding API。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.analysis.archive import HistoryUnavailable
from app.core.rag.retriever import BM25Index
from app.core.tools.base import BaseTool, ToolParameter
from app.infrastructure.database.models import SqlExample

EXAMPLE_PROMPT = """

## SQL 示例
- 写复杂 SQL 前可以用 search_sql_examples 查找用户确认过的同类问题写法，参考其中的表、关联和口径。
- 示例只说明写法，不是本轮证据：表结构可能已变化，问题条件也不同。仍须按当前问题改写并用 run_sql 实际执行。
- 示例中出现的数字不能写进回答。
"""

MAX_EXAMPLES = 2000


class SqlExampleError(ValueError):
    """用户可以理解并修正的错误。"""


def confirmed_sql(report: dict[str, Any]) -> list[str]:
    """报告中实际执行成功的 run_sql 语句，按执行顺序去重。"""
    statements = []
    for step in report.get("steps", []):
        if step.get("tool") == "run_sql" and step.get("ok"):
            sql = str((step.get("arguments") or {}).get("sql", "")).strip()
            if sql and sql not in statements:
                statements.append(sql)
    return statements


class SqlExampleStore:
    def __init__(self, engine):
        self.engine = engine

    async def _call(self, function, *args):
        try:
            return await asyncio.to_thread(function, *args)
        except SQLAlchemyError as exc:
            logger.warning("SQL 示例库操作未完成: {}", type(exc).__name__)
            raise HistoryUnavailable() from None

    async def check_ready(self) -> None:
        def check():
            with Session(self.engine) as session:
                session.execute(select(SqlExample.id).limit(0))

        await self._call(check)

    async def add_from_report(self, report: dict[str, Any], note: str = "") -> dict[str, Any]:
        """把一份已保存报告的问题和最后一条成功 SQL 存为示例；同一报告只保存一次。"""
        if report.get("reflection_status") == "failed":
            raise SqlExampleError("审查未通过的报告不能作为示例")
        statements = confirmed_sql(report)
        if not statements:
            raise SqlExampleError("这份报告没有成功执行的 SQL，无法保存为示例")
        question = str(report.get("question", "")).strip()
        if not question:
            raise SqlExampleError("报告缺少原始问题")

        def write():
            with Session(self.engine) as session:
                existing = session.scalar(
                    select(SqlExample).where(SqlExample.source_report_id == report["trace_id"])
                )
                if existing is not None:
                    return _public(existing), False
                example = SqlExample(
                    question=question[:2000],
                    sql=statements[-1],
                    note=note.strip()[:500],
                    source_report_id=report["trace_id"],
                    created_at=datetime.now(UTC),
                )
                session.add(example)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(
                        select(SqlExample).where(SqlExample.source_report_id == report["trace_id"])
                    )
                    return _public(existing), False
                return _public(example), True

        value, created = await self._call(write)
        return {**value, "created": created}

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        def read():
            with Session(self.engine) as session:
                rows = session.scalars(
                    select(SqlExample).order_by(SqlExample.created_at.desc()).limit(limit)
                )
                return [_public(row) for row in rows]

        return await self._call(read)

    async def delete(self, example_id: str) -> bool:
        def write():
            with Session(self.engine) as session, session.begin():
                row = session.get(SqlExample, example_id)
                if row is None:
                    return False
                session.delete(row)
                return True

        return await self._call(write)

    async def search(self, question: str, top_k: int = 3) -> list[dict[str, Any]]:
        def read():
            with Session(self.engine) as session:
                rows = list(
                    session.scalars(
                        select(SqlExample)
                        .order_by(SqlExample.created_at.desc())
                        .limit(MAX_EXAMPLES)
                    )
                )
            index = BM25Index()
            index.add_documents({row.id: f"{row.question} {row.note}" for row in rows})
            by_id = {row.id: row for row in rows}
            return [
                {**_public(by_id[example_id]), "score": round(score, 4)}
                for example_id, score in index.search(question, top_k)
            ]

        return await self._call(read)


def _public(row: SqlExample) -> dict[str, Any]:
    return {
        "id": row.id,
        "question": row.question,
        "sql": row.sql,
        "note": row.note,
        "source_report_id": row.source_report_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class SearchSqlExamplesTool(BaseTool):
    def __init__(self, store: SqlExampleStore) -> None:
        super().__init__()
        self.name = "search_sql_examples"
        self.description = (
            "查找用户确认过的同类问题及其 SQL 写法。示例只作参考，必须按当前问题改写后用 run_sql 执行，"
            "不能引用示例中的数字。"
        )
        self.parameters = [
            ToolParameter(name="question", type="string", description="当前要回答的问题"),
        ]
        self.store = store

    async def execute(self, **kwargs: Any) -> Any:
        question = str(kwargs.get("question") or "").strip()
        if not question:
            return {"examples": [], "note": "请提供问题"}
        examples = await self.store.search(question)
        return {
            "examples": [
                {"question": e["question"], "sql": e["sql"], "note": e["note"]} for e in examples
            ],
            "note": "仅供参考写法；表结构和条件可能不同，须重新执行查询"
            if examples
            else "没有相似的已确认示例",
        }
