"""
数据分析 Agent 的工具集。

典型调用顺序（由模型自行决定，不强制）：
list_tables → describe_tables → search_glossary → run_sql →（报错则修正后重试）→ calculator
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.analytics.engine import AnalyticsDatabase, SQLValidationError, TableInfo
from app.analytics.glossary import GlossaryStore
from app.core.rag.retriever import BM25Index
from app.core.tools.base import BaseTool, ToolError, ToolParameter


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    return v


def _table_text(table: TableInfo) -> str:
    """用于检索相关表的文本：表名、表注释、字段名和字段注释。"""
    parts = [table.qualified_name, table.comment]
    for column in table.columns:
        parts.extend((column.name, column.comment))
    return " ".join(part for part in parts if part)


def rank_tables(tables: list[TableInfo], question: str, limit: int) -> list[TableInfo]:
    """按与问题的 BM25 相关度排序；没有命中的表排在后面并保持原顺序。"""
    index = BM25Index()
    index.add_documents({t.qualified_name: _table_text(t) for t in tables})
    order = {name: rank for rank, (name, _) in enumerate(index.search(question, len(tables)))}
    ranked = sorted(tables, key=lambda t: order.get(t.qualified_name, len(order)))
    return ranked[:limit]


class ListTablesTool(BaseTool):
    def __init__(self, db: AnalyticsDatabase, *, max_tables: int = 30) -> None:
        super().__init__()
        self.name = "list_tables"
        self.description = (
            "列出业务库中的表名和列名。写 SQL 前先用它了解有哪些表。"
            f"表超过 {max_tables} 张时只返回与 question 最相关的部分，没找到需要的表就换个说法再查。"
        )
        self.parameters = [
            ToolParameter(
                name="question",
                type="string",
                description="要回答的问题或涉及的业务对象，例如「退款率」「用户注册区域」",
                required=False,
            )
        ]
        self._db = db
        self._max_tables = max(1, max_tables)

    async def execute(self, **kwargs: Any) -> Any:
        tables = await self._db.list_tables()
        total = len(tables)
        question = str(kwargs.get("question") or "").strip()
        if total > self._max_tables:
            if not question:
                raise ToolError(f"业务库共有 {total} 张表，请在 question 中说明要找的业务对象")
            tables = rank_tables(tables, question, self._max_tables)
        result: dict[str, Any] = {
            "dialect": self._db.dialect,
            "tables": [
                {"table": t.qualified_name, "columns": [c.name for c in t.columns]} for t in tables
            ],
        }
        if len(tables) < total:
            result["total_tables"] = total
            result["note"] = (
                f"共 {total} 张表，这里是与问题最相关的 {len(tables)} 张；没找到需要的表时换个说法再查"
            )
        return result


class DescribeTablesTool(BaseTool):
    def __init__(self, db: AnalyticsDatabase) -> None:
        super().__init__()
        self.name = "describe_tables"
        self.description = (
            "查看指定表的完整结构：字段类型、字段注释（含单位和枚举值含义）、外键，以及 3 行样例数据。"
            "写 SQL 之前必须先查看涉及到的表。"
        )
        self.parameters = [
            ToolParameter(
                name="tables",
                type="array",
                items={"type": "string"},
                description='表名列表，例如 ["orders", "users"]',
            )
        ]
        self._db = db

    async def execute(self, **kwargs: Any) -> Any:
        names = kwargs.get("tables")
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",")]
        if not names:
            raise ToolError("tables 不能为空")
        found, missing = await self._db.describe(list(names))
        parts: list[str] = []
        for t in found:
            sample = await self._db.sample_rows(t.qualified_name, limit=3)
            rows = "\n".join("  " + " | ".join(str(_jsonable(v)) for v in r) for r in sample.rows)
            parts.append(f"{t.render()}\n  样例 ({' | '.join(sample.columns)}):\n{rows}")
        if missing:
            all_names = [t.qualified_name for t in await self._db.list_tables()]
            parts.append(f"以下表不存在: {', '.join(missing)}。可用的表: {', '.join(all_names)}")
        return "\n\n".join(parts)


class SearchGlossaryTool(BaseTool):
    def __init__(self, store: GlossaryStore) -> None:
        super().__init__()
        self.name = "search_glossary"
        self.description = (
            "检索业务口径文档，获取指标的标准定义和对应的 SQL 写法（如 GMV、销售额、退款率、"
            "活跃用户、客单价、复购率、区域和渠道含义、金额单位、需要排除的数据）。"
            "问题涉及任何业务指标或业务术语时，写 SQL 前先查口径。"
        )
        self.parameters = [
            ToolParameter(
                name="query", type="string", description="要查的指标或术语，例如「退款率」"
            ),
        ]
        self._store = store

    async def execute(self, **kwargs: Any) -> Any:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            raise ToolError("query 不能为空")
        hits = await self._store.search(query, top_k=3)
        if not hits:
            return f"未找到与「{query}」相关的口径。已收录的条目: {', '.join(self._store.titles)}"
        return "\n\n".join(h.content for h in hits)


class RunSQLTool(BaseTool):
    def __init__(self, db: AnalyticsDatabase) -> None:
        super().__init__()
        self.name = "run_sql"
        self.description = (
            "在业务库上执行一条只读 SELECT 查询并返回结果。只允许 SELECT / WITH；"
            f"结果最多返回 {db.max_rows} 行，需要明细时请先聚合或加 LIMIT。"
            "出错时会返回数据库报错信息，请据此修正 SQL 后重试。"
        )
        self.parameters = [
            ToolParameter(
                name="sql", type="string", description=f"要执行的 SQL（{db.dialect} 方言）"
            ),
            ToolParameter(
                name="purpose",
                type="string",
                description="这条 SQL 要回答什么问题（一句话，便于追踪审查）",
                required=False,
            ),
        ]
        self._db = db

    async def execute(self, **kwargs: Any) -> Any:
        sql = str(kwargs.get("sql", ""))
        try:
            result = await self._db.execute(sql)
        except SQLValidationError as exc:
            raise ToolError(str(exc)) from exc
        out: dict[str, Any] = {
            "columns": result.columns,
            "rows": [[_jsonable(v) for v in r] for r in result.rows],
            "row_count": len(result.rows),
            "truncated": result.truncated,
        }
        if result.truncated:
            out["note"] = (
                f"结果超过 {self._db.max_rows} 行已截断，结论不能基于不完整数据，请改为聚合查询"
            )
        if not result.rows:
            out["note"] = (
                "查询结果为空。请检查过滤条件（日期范围、枚举值大小写、区域编码等）是否正确"
            )
        return out


def build_analytics_tools(
    db: AnalyticsDatabase, glossary: GlossaryStore, *, max_listed_tables: int = 30
) -> list[BaseTool]:
    from app.analytics.comparison import ComparePeriodsTool, PrepareAnalysisTool
    from app.analytics.metrics import GetMetricDefinitionTool, QueryMetricTool
    from app.analytics.quality import ProfileDatasetTool
    from app.core.tools.builtin.calculator import CalculatorTool

    return [
        ListTablesTool(db, max_tables=max_listed_tables),
        DescribeTablesTool(db),
        SearchGlossaryTool(glossary),
        RunSQLTool(db),
        CalculatorTool(),
        PrepareAnalysisTool(),
        ComparePeriodsTool(db),
        ProfileDatasetTool(db),
        GetMetricDefinitionTool(),
        QueryMetricTool(db),
    ]
