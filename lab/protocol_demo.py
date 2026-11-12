"""
第 04 课答疑：为什么 Agent 要用 Protocol 约定接口？

同一个 run_agent 函数，先后交给三个完全不同的「模型」，它都能跑，一行都不用改。
不需要 API Key（第三个是故意坏掉的模型，用来演示测试出错分支）。

在 project-python 目录下运行：
    python lab/protocol_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Protocol


# ------------------------------------------------------------ 1. 约定：岗位要求
class ChatLLM(Protocol):
    """只要求一件事：有一个 async chat(messages) 方法，返回一段文字。"""

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        ...


# ------------------------------------------------------------ 2. 使用方：只认约定
async def run_agent(llm: ChatLLM, question: str) -> str:
    """简化版 Agent：它不知道、也不关心 llm 背后到底是谁。"""
    messages = [{"role": "user", "content": question}]
    try:
        return await llm.chat(messages)
    except Exception as exc:  # 模型出错时返回失败信息，而不是让程序崩溃
        return f"[失败] 模型调用出错: {exc}"


# ------------------------------------------------------------ 3. 三个满足约定的「应聘者」
class RealLLM:
    """真模型（这里用打印代替真实的网络请求，避免花钱）。项目里对应 ModelRouter。"""

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return f"(假装请求了 DeepSeek) 关于「{messages[-1]['content']}」的回答……"


class ScriptedLLM:
    """剧本模型：按预先写好的台词依次返回。项目里对应 tests/conftest.py 的 ScriptedLLM。"""

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return self.script.pop(0)


class BrokenLLM:
    """故意坏掉的模型：模拟服务商宕机。真模型很难让它按你的要求出错，假模型一句话就行。"""

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        raise ConnectionError("服务商宕机了")


# 注意：上面三个类都没有继承 ChatLLM，也没有在任何地方「注册」，只是碰巧都有 chat 方法。


async def main() -> None:
    q = "上个月销售额是多少？"
    for llm in (RealLLM(), ScriptedLLM(["11 月净销售额 1,047,258.23 元"]), BrokenLLM()):
        answer = await run_agent(llm, q)
        print(f"{type(llm).__name__:<12} → {answer}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
