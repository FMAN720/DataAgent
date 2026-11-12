"""
第 04 课练习：用「剧本模型」驱动项目里真正的 ReActAgent，看清每一步 messages 怎么变长。

剧本模型不是真的大模型，它按预先写好的剧本依次返回「点菜」或「最终答案」。
工具、数据库、Agent 循环全是项目里的真实代码，所以不需要 API Key，每次运行结果完全一样。

在 project-python 目录下运行：
    python lab/trace_react.py            # 正常流程：查口径 → 跑 SQL → 作答
    python lab/trace_react.py --result   # 只看最后返回的 AgentResult
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

GOOD_SQL = (
    "SELECT ROUND(SUM(o.pay_amount) / 100.0, 2) AS sales FROM orders o JOIN users u USING(user_id) "
    "WHERE o.status IN ('paid','completed') AND u.is_test = 0 AND o.order_time LIKE '2025-11%'"
)


class ScriptedLLM:
    """
    剧本模型：满足 ReActAgent 需要的 ChatLLM 接口（有一个 async chat 方法），
    每被调用一次，就按顺序返回剧本里的下一条响应，并打印它收到的 messages。
    """

    def __init__(self, script: list[LLMResponse], verbose: bool = True) -> None:
        self.script = list(script)
        self.verbose = verbose
        self.n = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.n += 1
        if self.verbose:
            print(f"\n================ 第 {self.n} 次调用模型 ================")
            print(f"收到 {len(messages)} 条消息，带了 tools: {'是' if 'tools' in kwargs else '否'}")
            for i, m in enumerate(messages):
                print(f"  [{i}] {describe(m)}")
        resp = self.script.pop(0)
        if self.verbose:
            if resp.tool_calls:
                calls = ", ".join(f"{tc.name}({tc.arguments})" for tc in resp.tool_calls)
                print(f"模型决定 → 点菜: {calls}")
            else:
                print(f"模型决定 → 最终答案: {resp.content}")
        return resp


def describe(m: dict[str, Any]) -> str:
    role = m["role"]
    if m.get("tool_calls"):
        names = ", ".join(tc["function"]["name"] for tc in m["tool_calls"])
        return f"{role:<9} 点菜单: {names}"
    content = (m.get("content") or "").replace("\n", " ")
    if role == "tool":
        return f"{role:<9} [{m['tool_call_id']}] {content[:50]}..."
    return f"{role:<9} {content[:50]}"


def call(id_: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=id_, name=name, arguments=json.dumps(arguments, ensure_ascii=False))


def build_script() -> list[LLMResponse]:
    return [
        # 第 1 次：模型决定先查口径
        LLMResponse(content="先查销售额口径", tool_calls=[call("call_1", "search_glossary", query="销售额")]),
        # 第 2 次：看到口径后，写 SQL
        LLMResponse(content="按口径排除测试账号", tool_calls=[call("call_2", "run_sql", sql=GOOD_SQL)]),
        # 第 3 次：看到查询结果，给出最终答案（不再点菜）
        LLMResponse(content="2025 年 11 月净销售额为 1,047,258.23 元。口径：净销售额，已排除测试账号。"),
    ]


async def main() -> None:
    only_result = "--result" in sys.argv
    llm = ScriptedLLM(build_script(), verbose=not only_result)
    agent = ReActAgent(llm, build_registry(), max_steps=8)

    messages = [
        {"role": "system", "content": "你是严谨的电商数据分析师……（这里用简短版代替真实 system prompt）"},
        {"role": "user", "content": "上个月的销售额是多少？"},
    ]

    async def on_step(event: dict[str, Any]) -> None:
        if not only_result:
            print(f"    ↳ 事件 on_step: type={event['type']}, step={event['step']}"
                  + (f", tool={event['tool']}, ok={event['ok']}" if event["type"] == "tool" else ""))

    result = await agent.run(messages, on_step=on_step)

    print("\n================ AgentResult ================")
    print(f"success:      {result.success}")
    print(f"final_answer: {result.final_answer}")
    print(f"llm_calls:    {result.llm_calls}")
    print(f"error:        {result.error}")
    print(f"steps:        共 {len(result.steps)} 个 ToolStep")
    for s in result.steps:
        print(f"  - step={s.step} tool={s.tool} ok={s.ok} duration_ms={s.duration_ms} thought={s.thought!r}")
    print(f"new_messages: 共 {len(result.new_messages)} 条（本轮新增，不含传入的 {len(messages)} 条）")
    for m in result.new_messages:
        print(f"  - {describe(m)}")
    print(f"\n传入的 messages 还是 {len(messages)} 条吗？ {len(messages) == 2}  （run 不会修改传入的列表）")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(main())
