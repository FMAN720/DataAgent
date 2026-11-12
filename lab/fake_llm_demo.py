"""
第 04 课答疑：假模型（剧本模型）为什么有用？

假模型测的不是「模型聪不聪明」，而是「模型周围的代码对不对」。
这里写了两个 Agent 循环：一个正确，一个藏了初学者常犯的 bug。
用同一个剧本模型去考它们，看假模型能不能把 bug 抓出来。

工具和数据库都是项目里的真实代码，不需要 API Key，每次结果都一样。

在 project-python 目录下运行：
    python lab/fake_llm_demo.py
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

from app.infrastructure.llm.model_router import LLMResponse, ToolCall  # noqa: E402

GOOD_SQL = (
    "SELECT ROUND(SUM(o.pay_amount) / 100.0, 2) FROM orders o JOIN users u USING(user_id) "
    "WHERE o.status IN ('paid','completed') AND u.is_test = 0 AND o.order_time LIKE '2025-11%'"
)


# ------------------------------------------------------------------ 剧本模型
class ScriptedLLM:
    """
    按剧本依次返回响应。剧本里的一项也可以是函数：它能先「检查」Agent 发来的 messages，
    再决定返回什么。这样假模型既是演员，也是监考员。
    """

    def __init__(self, script: list) -> None:
        self.script = list(script)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        item = self.script.pop(0)
        return item(messages) if callable(item) else item


def call(id_: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=id_, name=name, arguments=json.dumps(arguments, ensure_ascii=False))


def make_script() -> list:
    """剧本：模型先写一条错 SQL，看到报错后改对，最后作答。"""

    def check_error_was_seen(messages):
        # 监考：模型此时必须能在最后一条 tool 消息里看到报错，否则它根本不知道自己写错了
        last = messages[-1]
        assert last["role"] == "tool" and "no such column" in last["content"], (
            f"Agent 没有把 SQL 报错交还给模型！模型最后看到的是：{last['role']} {last.get('content')!r}"
        )
        return LLMResponse(content="字段写错了，改一下", tool_calls=[call("c2", "run_sql", sql=GOOD_SQL)])

    def answer_from_result(messages):
        value = json.loads(messages[-1]["content"])["rows"][0][0]
        return LLMResponse(content=f"11 月净销售额为 {value:.2f} 元")

    return [
        LLMResponse(content="", tool_calls=[call("c1", "run_sql", sql="SELECT SUM(amount) FROM orders")]),
        check_error_was_seen,
        answer_from_result,
    ]


# ------------------------------------------------------------------ 两个 Agent 循环
async def good_agent(llm, registry, question: str) -> str:
    """正确写法：工具失败了，也把错误信息作为 tool 消息交还给模型。"""
    messages = [{"role": "user", "content": question}]
    for _ in range(8):
        resp = await llm.chat(messages)
        if not resp.tool_calls:
            return resp.content
        messages.append(resp.to_assistant_message())
        for tc in resp.tool_calls:
            outcome = await registry.invoke(tc.name, json.loads(tc.arguments))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})
    return "超过步数"


async def buggy_agent(llm, registry, question: str) -> str:
    """
    有 bug 的写法：开发者觉得「失败的结果没用」，只把成功的结果交还给模型。
    用真模型测时，这个 bug 很可能看不出来：模型偶尔会自己猜对字段名，答案照样正确。
    """
    messages = [{"role": "user", "content": question}]
    for _ in range(8):
        resp = await llm.chat(messages)
        if not resp.tool_calls:
            return resp.content
        messages.append(resp.to_assistant_message())
        for tc in resp.tool_calls:
            outcome = await registry.invoke(tc.name, json.loads(tc.arguments))
            if outcome.ok:                                   # ← bug：失败的结果被吞掉了
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})
    return "超过步数"


async def exam(agent, label: str) -> None:
    print(f"===== 考试：{label} =====")
    try:
        answer = await agent(ScriptedLLM(make_script()), build_registry(), "上个月销售额多少")
        assert "1047258.23" in answer, f"答案不对：{answer}"
        print(f"通过 ✔  最终答案：{answer}\n")
    except AssertionError as exc:
        print(f"不通过 ✘  {exc}\n")


async def main() -> None:
    await exam(good_agent, "正确的 Agent 循环")
    await exam(buggy_agent, "吞掉失败结果的 Agent 循环")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(main())
