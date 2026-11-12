"""
第 05 课练习：用假模型逐个触发 react_agent.py 里的「出错分支」，看项目代码怎么应对。

    python lab/error_branches.py 1   # 情况 1：模型给的参数不是合法 JSON
    python lab/error_branches.py 2   # 情况 2：模型调用了不存在的工具 / 漏了必填参数
    python lab/error_branches.py 3   # 情况 3：模型调用失败（服务商宕机）
    python lab/error_branches.py 4   # 情况 4：模型一直点菜停不下来（步数上限）
    python lab/error_branches.py 5   # 情况 5：一次点多道菜，并发执行
    python lab/error_branches.py 6   # 情况 6：参数是合法 JSON，但不是对象（练习 2）
    python lab/error_branches.py all # 全部跑一遍

Agent、工具、数据库全是项目里的真实代码，只有模型是假的。不需要 API Key。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger  # noqa: E402
from tools_cli import build_registry  # noqa: E402

from app.core.agent.react_agent import ReActAgent  # noqa: E402
from app.infrastructure.llm.model_router import LLMResponse, ToolCall  # noqa: E402


class FakeLLM:
    """假模型：按剧本念台词；每次被调用都打印它收到了什么、带没带工具菜单。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.n = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.n += 1
        last = messages[-1]
        last_text = (last.get("content") or "").replace("\n", " ")[:70]
        print(f"  假模型第 {self.n} 次被调用：收到 {len(messages)} 条消息，"
              f"带工具菜单：{'是' if 'tools' in kwargs else '否'}")
        print(f"      最后一条是 {last['role']}：{last_text}")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            print(f"      → 假模型抛出异常：{item}")
            raise item
        return item


def call(name: str, arguments: str | dict, id_: str = "") -> ToolCall:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return ToolCall(id=id_ or f"call_{name}", name=name, arguments=args)


def show(result) -> None:
    print("  ---- AgentResult ----")
    print(f"  success={result.success}  llm_calls={result.llm_calls}  error={result.error}")
    for s in result.steps:
        print(f"  step={s.step} tool={s.tool} ok={s.ok} duration_ms={s.duration_ms}")
        print(f"      output: {s.output.replace(chr(10), ' ')[:80]}")
    if result.final_answer:
        print(f"  final_answer: {result.final_answer}")
    print()


async def run(script: list, max_steps: int = 8):
    agent = ReActAgent(FakeLLM(script), build_registry(), max_steps=max_steps)
    return await agent.run([{"role": "user", "content": "上个月销售额多少"}])


async def case1() -> None:
    print("===== 情况 1：模型给的参数不是合法 JSON（react_agent.py 第 97～104、204～206 行）=====")
    show(await run([
        LLMResponse(tool_calls=[call("run_sql", '{"sql": "SELECT 1"')]),   # 少了一个 }
        LLMResponse(content="参数写错了，我重新来"),
    ]))


async def case2() -> None:
    print("===== 情况 2：调用不存在的工具 / 漏了必填参数（registry.py 第 92～102 行）=====")
    show(await run([
        LLMResponse(tool_calls=[call("delete_all_data", {})]),            # 菜单里没有这个工具
        LLMResponse(tool_calls=[call("run_sql", {"purpose": "查销售额"})]),  # 漏了必填的 sql
        LLMResponse(content="好的，我换个方法"),
    ]))


async def case3() -> None:
    print("===== 情况 3：模型调用失败（react_agent.py 第 163～175 行）=====")
    show(await run([
        LLMResponse(tool_calls=[call("list_tables", {})]),
        ConnectionError("服务商宕机了"),                                   # 第 2 次调用时模型挂了
    ]))


async def case4() -> None:
    print("===== 情况 4：模型一直点菜停不下来，max_steps=3（react_agent.py 第 150～160 行）=====")
    show(await run([
        LLMResponse(tool_calls=[call("list_tables", {})]),
        LLMResponse(tool_calls=[call("list_tables", {})]),
        LLMResponse(content="基于已有信息：库里有 10 张表，未能进一步核实销售额"),
    ], max_steps=3))


async def case5() -> None:
    print("===== 情况 5：一次点三道菜，并发执行（react_agent.py 第 203～214 行）=====")
    t0 = time.perf_counter()
    show(await run([
        LLMResponse(tool_calls=[
            call("search_glossary", {"query": "销售额"}, "call_a"),
            call("describe_tables", {"tables": ["orders"]}, "call_b"),
            call("run_sql", {"sql": "SELECT category FROM orders"}, "call_c"),   # 这一道会失败
        ]),
        LLMResponse(content="三道菜都看到了"),
    ]))
    print(f"  总耗时 {int((time.perf_counter() - t0) * 1000)} ms\n")


async def case6() -> None:
    print("===== 情况 6：参数是合法 JSON，但不是对象（练习 2：_parse_arguments 的第二个检查）=====")
    result = await run([
        # 字符串会由 call 原样写入 ToolCall.arguments；JSON 数组能解析，但不是参数对象。
        LLMResponse(tool_calls=[call("run_sql", '[1, 2, 3]')]),
        # 这句是预先写好的剧本，用于演示 Agent 收到错误后仍可继续运行。
        LLMResponse(content="我把参数写成数组了，应该使用包含 sql 的对象。"),
    ])
    show(result)
    assert not result.steps[0].ok
    assert json.loads(result.steps[0].output)["error"] == "参数必须是 JSON 对象"

# # 让假模型调用 `run_sql`，参数是一个合法的 JSON，但不是对象，比如 `'[1, 2, 3]'`
# async def case7() -> None:
#     print("===== 情况 7：让假模型调用 `run_sql`，参数是合法的 JSON，但不是对象 =====")
#     result = await run([
#         LLMResponse(tool_calls=[call("run_sql", '[1, 2, 3]')]),
#         LLMResponse(content="我把参数写成数组了，应该使用包含 sql 的对象。"),
#     ])
#     show(result)
#     assert not result.steps[0].ok
#     assert json.loads(result.steps[0].output)["error"] == "参数必须是 JSON 对象"


CASES = {"1": case1, "2": case2, "3": case3, "4": case4, "5": case5, "6": case6}


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which == "all":
        for fn in CASES.values():
            await fn()
    elif which in CASES:
        await CASES[which]()
    else:
        print(__doc__)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(main())
