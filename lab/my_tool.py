"""
第 06 课练习：自己设计一个新工具 column_values（查看某个字段有哪些取值）。

为什么需要它？模型写 WHERE 条件时经常猜错枚举值：
  - 区域存的是 EAST，模型写成 '华东'
  - 状态是 completed，模型写成 'Completed' 或 '已完成'
现在模型只能从 describe_tables 的 3 行样例里猜，或者自己写一条 DISTINCT 查询。
这个工具直接告诉它：这个字段一共有哪些值、各有多少条。

本文件只是练习，不修改项目代码。运行：
    python lab/my_tool.py              # 直接调用新工具，看输出和各种出错情况
    python lab/my_tool.py --schema     # 看新工具导出给模型的 schema
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger  # noqa: E402
from tools_cli import build_registry  # noqa: E402

from app.analytics.engine import AnalyticsDatabase, SQLValidationError  # noqa: E402
from app.core.tools.base import BaseTool, ToolError, ToolParameter  # noqa: E402


class ColumnValuesTool(BaseTool):
    MAX_VALUES = 30   # 取值太多（比如 user_id）就没意义了，只返回前 30 个并提示

    def __init__(self, db: AnalyticsDatabase) -> None:
        super().__init__()
        self.name = "column_values"
        self.description = (
            "查看某张表某个字段的全部取值及每个值的行数（按行数从多到少，最多 30 个）。"
            "写 WHERE 条件过滤枚举类字段（状态、区域、渠道、等级等）之前用它确认真实取值，"
            "避免把 'EAST' 写成 '华东'、把 'completed' 写成 '已完成'。不适合 id、金额、时间这类取值很多的字段。"
        )
        self.parameters = [
            ToolParameter(name="table", type="string", description="表名，例如 \"orders\""),
            ToolParameter(name="column", type="string", description="字段名，例如 \"status\""),
        ]
        self._db = db

    async def execute(self, **kwargs: Any) -> Any:
        table = str(kwargs.get("table", "")).strip()
        column = str(kwargs.get("column", "")).strip()

        # 表名、字段名要拼进 SQL，必须先用白名单校验，否则就是 SQL 注入
        found, missing = await self._db.describe([table])
        if missing:
            all_names = [t.name for t in await self._db.list_tables()]
            raise ToolError(f"表不存在: {table}。可用的表: {', '.join(all_names)}")
        info = found[0]
        col_names = [c.name for c in info.columns]
        if column not in col_names:
            raise ToolError(f"表 {info.name} 没有字段 {column}。可用的字段: {', '.join(col_names)}")

        sql = (
            f'SELECT "{column}" AS value, COUNT(*) AS cnt FROM "{info.name}" '
            f'GROUP BY "{column}" ORDER BY cnt DESC LIMIT {self.MAX_VALUES + 1}'
        )
        try:
            result = await self._db.execute(sql)
        except SQLValidationError as exc:
            raise ToolError(str(exc)) from exc

        rows = result.rows[: self.MAX_VALUES]
        out: dict[str, Any] = {
            "table": info.name,
            "column": column,
            "values": [{"value": v, "count": c} for v, c in rows],
        }
        comment = next((c.comment for c in info.columns if c.name == column), "")
        if comment:
            out["comment"] = comment
        if len(result.rows) > self.MAX_VALUES:
            out["note"] = f"取值超过 {self.MAX_VALUES} 个，只列出最多的 {self.MAX_VALUES} 个。这个字段可能不是枚举类字段"
        return out


async def demo() -> None:
    registry = build_registry()
    db = registry.get_tool("run_sql")._db        # 复用项目里已经建好的只读数据库连接
    registry.register(ColumnValuesTool(db))

    if "--schema" in sys.argv:
        print(json.dumps(registry.get_tool("column_values").to_openai_tool(), ensure_ascii=False, indent=2))
        return

    cases = [
        ("正常：区域编码", {"table": "users", "column": "region_code"}),
        ("正常：订单状态", {"table": "orders", "column": "status"}),
        ("取值太多：user_id", {"table": "orders", "column": "user_id"}),
        ("表名写错", {"table": "order", "column": "status"}),
        ("字段名写错", {"table": "orders", "column": "region"}),
        ("漏了参数", {"table": "orders"}),
        ("想注入", {"table": "orders", "column": "status\" FROM orders; DROP TABLE orders; --"}),
    ]
    for label, args in cases:
        outcome = await registry.invoke("column_values", args)
        content = outcome.content
        if len(content) > 300:
            content = content[:300] + "……"
        print(f"===== {label} =====")
        print(f"ok={outcome.ok}")
        print(content + "\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(demo())
