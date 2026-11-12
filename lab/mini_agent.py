"""
第 03 课：最小 Agent。核心循环只有 run_agent 这一个函数。

它直接调用项目里真实的 5 个工具（查表、查口径、跑 SQL、计算器），
和第 01 课你手动做的事情一样，只是这次做决定的是模型。

在 project-python 目录下运行：
    python lab/mini_agent.py "上个月的销售额是多少？"
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.analytics.prompts import build_system_prompt  # noqa: E402
from app.config import get_settings  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools_cli import build_registry  # noqa: E402  复用第 01 课的工具注册


def run_agent(client: OpenAI, model: str, registry, question: str, max_steps: int = 10) -> str:
    tools = registry.to_openai_tools()            # 菜单：5 个工具的 schema
    messages = [
        {"role": "system", "content": build_system_prompt("sqlite", "2025-12-31")},
        {"role": "user", "content": question},
    ]

    for step in range(1, max_steps + 1):
        resp = client.chat.completions.create(model=model, messages=messages, tools=tools, temperature=0)
        msg = resp.choices[0].message

        # 模型没有点菜 → 它认为信息够了，这就是最终答案
        if not msg.tool_calls:
            return msg.content or ""

        # 模型点了菜 → 先把点菜单记进对话
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        # 程序逐个做菜，把结果带着 tool_call_id 端回去
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            outcome = asyncio.run(registry.invoke(tc.function.name, args))
            preview = outcome.content.replace("\n", " ")[:60]     # 只打印开头，方便看过程
            print(f"[第 {step} 轮] {tc.function.name}({json.dumps(args, ensure_ascii=False)[:100]})"
                  f" → {'成功' if outcome.ok else '失败'}: {preview}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": outcome.content})

    return "已达到最大步数，未能得出结论。"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    question = " ".join(sys.argv[1:]) or "上个月的销售额是多少？"
    s = get_settings()
    client = OpenAI(api_key=s.openai_api_key, base_url=s.openai_api_base or None)
    print(f"问题：{question}\n")
    answer = run_agent(client, s.openai_model, build_registry(), question)
    print(f"\n最终回答：\n{answer}")
