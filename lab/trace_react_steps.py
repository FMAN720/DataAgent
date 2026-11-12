"""
第 04 课答疑：trace_react.py 到底是怎么运行起来的？

这是 trace_react.py 的「解说版」：逻辑完全一样，但每执行一步都会打印一句话，
告诉你「现在是谁在运行、运行到哪一行、接下来要跳到哪里」。

缩进表示「谁调用了谁」：缩进越深，说明被调用得越深；缩进变浅，说明函数执行完、回到了调用它的地方。

不需要 API Key。在 project-python 目录下运行：
    python lab/trace_react_steps.py
"""

# ============================================================================
# 第一阶段：Python 从上往下「读」这个文件。
# 读到 import 会去加载别的文件；读到 def / class 只是「记住有这么个东西」，并不执行里面的代码。
# ============================================================================

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print("【读文件】Python 开始从上往下读 trace_react_steps.py")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))                       # 让下面能 import 到 app 里的项目代码
sys.path.insert(0, str(Path(__file__).resolve().parent))    # 让下面能 import 到 lab 里的 tools_cli

from loguru import logger  # noqa: E402
from tools_cli import build_registry  # noqa: E402

from app.core.agent.react_agent import ReActAgent  # noqa: E402
from app.infrastructure.llm.model_router import LLMResponse, ToolCall  # noqa: E402

print("【读文件】import 完成：拿到了项目里真正的 ReActAgent（来自 app/core/agent/react_agent.py）")

logger.remove()
depth = 0          # 当前缩进层数，用来显示「谁调用了谁」
step_no = 0        # 步骤编号


def say(text: str) -> None:
    """打印一句解说，带编号和缩进。"""
    global step_no
    step_no += 1
    print(f"{'    ' * depth}[{step_no:>2}] {text}")


GOOD_SQL = (
    "SELECT ROUND(SUM(o.pay_amount) / 100.0, 2) AS sales FROM orders o JOIN users u USING(user_id) "
    "WHERE o.status IN ('paid','completed') AND u.is_test = 0 AND o.order_time LIKE '2025-11%'"
)


class FakeLLM:
    """假模型：被调用一次，就从台词本里取出下一句交回去。"""

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.n = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        global depth
        self.n += 1
        depth += 1
        say(f"【跳进 本文件 FakeLLM.chat】假模型第 {self.n} 次被调用，收到 {len(messages)} 条消息")
        resp = self.script.pop(0)                          # ← 核心：取出下一句台词
        if resp.tool_calls:
            say(f"FakeLLM.chat：从台词本取出第 {self.n} 句「点菜 {resp.tool_calls[0].name}」，"
                f"台词本还剩 {len(self.script)} 句")
        else:
            say(f"FakeLLM.chat：从台词本取出第 {self.n} 句「最终答案」，台词本还剩 {len(self.script)} 句")
        say("FakeLLM.chat：return，把这句台词交回给 react_agent.py")
        depth -= 1
        return resp


print("【读文件】记住了 FakeLLM 这个类（只是定义，里面的代码还没运行）")


class NarratedTools:
    """把项目的工具注册表包一层，只为了在执行工具时打印一句解说，工具本身还是真的。"""

    def __init__(self, registry) -> None:
        self._registry = registry

    def to_openai_tools(self, names=None):
        return self._registry.to_openai_tools(names)

    async def invoke(self, name: str, arguments: dict[str, Any]):
        global depth
        depth += 1
        say(f"【跳进 工具注册表 invoke】执行真实工具 {name}（真的去查口径文档 / 真的查数据库）")
        outcome = await self._registry.invoke(name, arguments)
        preview = outcome.content.replace("\n", " ")[:40]
        say(f"工具执行完：{'成功' if outcome.ok else '失败'}，结果开头是「{preview}…」，交回给 react_agent.py")
        depth -= 1
        return outcome


print("【读文件】记住了 NarratedTools 这个类（还没运行）")


def build_script() -> list[LLMResponse]:
    def call(id_, name, **arguments):
        return ToolCall(id=id_, name=name, arguments=json.dumps(arguments, ensure_ascii=False))

    return [
        LLMResponse(content="先查口径", tool_calls=[call("call_1", "search_glossary", query="销售额")]),
        LLMResponse(content="写 SQL", tool_calls=[call("call_2", "run_sql", sql=GOOD_SQL)]),
        LLMResponse(content="2025 年 11 月净销售额为 1,047,258.23 元。"),
    ]


print("【读文件】记住了 build_script 函数（还没运行）")


async def main() -> None:
    global depth
    say("【main 开始】现在才真正开始干活")

    script = build_script()
    say(f"main：调用 build_script()，拿到台词本，共 {len(script)} 句")

    llm = FakeLLM(script)
    say("main：创建假模型 llm = FakeLLM(台词本)")

    tools = NarratedTools(build_registry())
    say("main：创建工具注册表（连上真实的演示数据库，加载真实的口径文档）")

    agent = ReActAgent(llm, tools, max_steps=8)
    say("main：创建 agent = ReActAgent(llm, tools)  ← 把假模型交给了项目里真正的 Agent")

    messages = [
        {"role": "system", "content": "你是严谨的电商数据分析师……"},
        {"role": "user", "content": "上个月的销售额是多少？"},
    ]
    say("main：准备好 messages（system + 用户问题，共 2 条）")

    say("main：调用 agent.run(messages)  →  下面跳进 app/core/agent/react_agent.py 的 run 方法")
    depth += 1
    say("【跳进 react_agent.py run】从这里开始，执行的是项目代码；"
        "它每次走到第 164 行 self._llm.chat(...)，就会跳回本文件的 FakeLLM.chat")
    result = await agent.run(messages)
    say("react_agent.py run：模型没有再点菜，循环结束，return 一个 AgentResult")
    depth -= 1

    say(f"【回到 main】拿到 result：success={result.success}，调了 {result.llm_calls} 次模型，"
        f"执行了 {len(result.steps)} 个工具")
    say(f"main：最终答案「{result.final_answer}」")
    say("【main 结束】")


print("【读文件】记住了 main 函数（还没运行）")
print("【读文件】读到文件最后的 if __name__ == '__main__'，下面开始真正运行\n")

# ============================================================================
# 第二阶段：真正开始运行。
# 直接运行这个文件时，__name__ 的值是 "__main__"，所以下面这行会执行。
# main 是 async 函数，不能直接 main()，要交给 asyncio.run 去运行。
# ============================================================================
if __name__ == "__main__":
    asyncio.run(main())
