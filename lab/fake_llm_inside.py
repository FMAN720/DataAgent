"""
第 04 课答疑：假模型不会思考，它的「回答」从哪来？

实验 1：问一个和剧本完全无关的问题，假模型照样念台词，证明它根本没看懂问题。
实验 2：剧本里放一个函数，它从真实的工具结果里取出数字，拼成答案。
        数字是数据库算的，假模型只是「抄」了一下。

不需要 API Key。在 project-python 目录下运行：
    python lab/fake_llm_inside.py
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

from app.core.agent.react_agent import ReActAgent  # noqa: E402
from app.infrastructure.llm.model_router import LLMResponse, ToolCall  # noqa: E402

SQL = (
    "SELECT ROUND(SUM(o.pay_amount) / 100.0, 2) FROM orders o JOIN users u USING(user_id) "
    "WHERE o.status IN ('paid','completed') AND u.is_test = 0 AND o.order_time LIKE '2025-11%'"
)


class ScriptedLLM:
    """整个「模型」就这几行：每被调用一次，从剧本里取出下一句。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.n = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.n += 1
        item = self.script.pop(0)                       # ← 没有思考，只是取下一句
        resp = item(messages) if callable(item) else item
        what = f"点菜 {resp.tool_calls[0].name}" if resp.tool_calls else f"回答「{resp.content}」"
        print(f"  假模型第 {self.n} 次被调用：收到 {len(messages)} 条消息 → 念剧本第 {self.n} 句：{what}")
        return resp


async def run(question: str, script: list) -> None:
    print(f"问题：{question}")
    result = await ReActAgent(ScriptedLLM(script), build_registry()).run([{"role": "user", "content": question}])
    print(f"最终答案：{result.final_answer}\n")


async def main() -> None:
    sql_call = ToolCall(id="c1", name="run_sql", arguments=json.dumps({"sql": SQL}))

    print("===== 实验 1：剧本是写死的文字 =====")
    fixed_script = [
        LLMResponse(tool_calls=[sql_call]),
        LLMResponse(content="11 月净销售额为 1,047,258.23 元"),   # 这个数字是写剧本的人提前查好、手写进来的
    ]
    await run("今天北京天气怎么样？", fixed_script)              # 问题和剧本毫无关系

    print("===== 实验 2：剧本最后一句是一个函数，从工具结果里取数字 =====")

    def answer_from_tool_result(messages):
        last_tool = messages[-1]                                 # Agent 交还回来的最后一条 tool 消息
        value = json.loads(last_tool["content"])["rows"][0][0]   # 取出数据库真实算出来的数
        return LLMResponse(content=f"11 月净销售额为 {value:,.2f} 元")

    await run("上个月销售额是多少？", [LLMResponse(tool_calls=[sql_call]), answer_from_tool_result])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(main())
