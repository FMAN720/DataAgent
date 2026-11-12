"""
课程练习工具：在命令行里手动调用 Agent 的工具，体验「自己当 Agent」。

用的是项目里真实的 ToolRegistry 和工具，和 Agent 调用时拿到的结果完全一样。
不需要 API Key。

在 project-python 目录下运行：
    python lab/tools_cli.py list_tables
    python lab/tools_cli.py describe_tables orders users
    python lab/tools_cli.py search_glossary 销售额
    python lab/tools_cli.py run_sql "SELECT COUNT(*) FROM orders"
    python lab/tools_cli.py calculator "1047258.23 / 1148843.98"
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 让脚本能 import 到 app 包（脚本在 lab/ 下，项目根目录是上一级）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.tools.registry import ToolRegistry  # noqa: E402
from app.demo import ensure_demo_db  # noqa: E402

# 每个工具的命令行参数怎么转成工具需要的 arguments 字典
ARG_BUILDERS = {
    "list_tables": lambda args: {},
    "describe_tables": lambda args: {"tables": args},
    "search_glossary": lambda args: {"query": " ".join(args)},
    "run_sql": lambda args: {"sql": " ".join(args)},
    "calculator": lambda args: {"expression": " ".join(args)},
}


def build_registry() -> ToolRegistry:
    settings = get_settings()
    if settings.analytics_db_url.startswith("sqlite:///"):
        ensure_demo_db(Path(settings.analytics_db_url[len("sqlite:///"):]))
    db = AnalyticsDatabase(settings.analytics_db_url, max_rows=settings.sql_max_rows)
    glossary = GlossaryStore.from_file(settings.glossary_path)
    registry = ToolRegistry()
    for tool in build_analytics_tools(db, glossary):
        registry.register(tool)
    return registry


async def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ARG_BUILDERS:
        print(__doc__)
        print("可用工具:", ", ".join(ARG_BUILDERS))
        return
    name, rest = sys.argv[1], sys.argv[2:]
    arguments = ARG_BUILDERS[name](rest)

    registry = build_registry()
    outcome = await registry.invoke(name, arguments)

    print(f"调用工具: {name}")
    print(f"参数:     {json.dumps(arguments, ensure_ascii=False)}")
    print(f"成功:     {outcome.ok}    耗时: {outcome.duration_ms} ms")
    print("-" * 60)
    # 这段字符串就是 Agent 循环里回传给模型的 role=tool 消息内容
    try:
        print(json.dumps(json.loads(outcome.content), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(outcome.content)


if __name__ == "__main__":
    # Windows 终端默认编码可能不是 UTF-8，统一一下避免中文乱码
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()  # 关掉「已注册工具」之类的日志，让输出更干净
    asyncio.run(main())
