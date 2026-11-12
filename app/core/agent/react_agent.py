"""
ReAct Agent（原生 function calling 版）。

循环结构：
    messages = [system, 历史对话..., user]
    while 步数 < 上限:
        resp = LLM(messages, tools)
        if resp 没有 tool_calls: 返回 resp.content 作为最终答案
        把 assistant 消息（含 tool_calls）追加到 messages
        逐个执行工具，把结果作为 role=tool 消息追加（带 tool_call_id）

和文本解析式 ReAct（Thought/Action/Observation 正则）相比：
- 工具名、参数由模型 API 结构化返回，不需要正则，参数 schema 由 tools 定义约束；
- 支持一次返回多个并行 tool_calls；
- 工具报错作为普通 observation 回传，模型可以据此修正参数（SQL 自我纠错就靠这一点）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger

from app.core.tools.registry import ToolOutcome
from app.infrastructure.llm.model_router import LLMResponse, ToolCall


class ChatLLM(Protocol):
    """Agent 依赖的最小 LLM 接口（ModelRouter 即满足）。"""

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse: ...


class ToolExecutor(Protocol):
    """Agent 依赖的最小工具接口（ToolRegistry 即满足）。"""

    def to_openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]: ...

    async def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


StepCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ToolStep:
    """一次工具调用的记录。"""

    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    output: str
    duration_ms: int
    thought: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool",
            "step": self.step,
            "tool": self.tool,
            "arguments": self.arguments,
            "ok": self.ok,
            "output": self.output,
            "duration_ms": self.duration_ms,
        }


@dataclass
class AgentResult:
    """ReAct 单次运行结果。"""

    success: bool
    final_answer: str
    steps: list[ToolStep] = field(default_factory=list)
    error: str | None = None
    llm_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    # 本轮新增的消息（assistant / tool），调用方可写回会话历史
    new_messages: list[dict[str, Any]] = field(default_factory=list)
    needs_clarification: bool = False

    @property
    def observations(self) -> list[str]:
        """成功的工具输出，供反思阶段作为证据。"""
        return [s.output for s in self.steps if s.ok]


def _parse_arguments(tc: ToolCall) -> tuple[dict[str, Any] | None, str | None]:
    try:
        args = json.loads(tc.arguments or "{}")
    except json.JSONDecodeError as exc:
        return None, f"参数不是合法 JSON: {exc}"
    if not isinstance(args, dict):
        return None, "参数必须是 JSON 对象"
    return args, None


# 必须先于同批其他工具执行的「前置检查」工具；它们要求澄清或失败时，本批其余工具不执行
_GATE_TOOLS = ("prepare_analysis", "profile_dataset", "query_metric", "query_business_metric")


def _refused(step: int, tc: ToolCall, thought: str, error: str) -> ToolStep:
    """未执行的工具调用：以错误 observation 回传给模型。"""
    return ToolStep(
        step, tc.name, {}, False, json.dumps({"error": error}, ensure_ascii=False), 0, thought
    )


class ReActAgent:
    """思考 → 调用工具 → 观察，直到模型不再调用工具或达到步数上限。"""

    def __init__(
        self,
        llm: ChatLLM,
        tools: ToolExecutor,
        *,
        max_steps: int = 12,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self.max_steps = max(1, max_steps)
        self.temperature = temperature
        self.model = model

    async def run(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        tool_names: list[str] | None = None,
        on_step: StepCallback | None = None,
        on_checkpoint: StepCallback | None = None,
    ) -> AgentResult:
        """
        :param messages: 完整输入（system + 历史 + 本轮 user），不会被原地修改
        :param tool_names: 允许使用的工具子集，None 表示全部
        :param on_step: 每产生一个事件就回调一次，用于流式输出和追踪
        """
        convo: list[dict[str, Any]] = list(messages)
        start_len = len(convo)
        tools_schema = self._tools.to_openai_tools(tool_names)
        allowed_tools = {tool["function"]["name"] for tool in tools_schema}
        steps: list[ToolStep] = []
        usage: dict[str, int] = {}

        async def emit(event: dict[str, Any]) -> None:
            # Durable checkpoints are part of execution, not a best-effort UI callback.
            if on_checkpoint is not None:
                await on_checkpoint(event)
            if on_step is not None:
                try:
                    await on_step(event)
                except Exception:  # noqa: BLE001 - 回调失败不能影响 Agent 本身
                    logger.exception("on_step 回调失败")

        for llm_call in range(1, self.max_steps + 1):
            # 最后一步不再提供工具，强制模型基于已有信息作答，避免直接以「步数耗尽」失败
            last_chance = llm_call == self.max_steps
            kwargs: dict[str, Any] = {"temperature": self.temperature}
            if tools_schema and not last_chance:
                kwargs["tools"] = tools_schema
            if last_chance:
                convo.append(
                    {
                        "role": "system",
                        "content": "已达到工具调用次数上限。请基于目前已获得的数据直接给出结论，并说明哪些部分未能核实。",
                    }
                )

            t0 = time.perf_counter()
            try:
                resp = await self._llm.chat(convo, model_preference=self.model, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLM 调用失败")
                return AgentResult(
                    success=False,
                    final_answer="",
                    steps=steps,
                    error=f"模型调用失败: {exc}",
                    llm_calls=llm_call,
                    usage=usage,
                    new_messages=convo[start_len:],
                )
            for k, v in (resp.usage or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
            await emit(
                {
                    "type": "llm",
                    "step": llm_call,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "tool_calls": [tc.name for tc in resp.tool_calls],
                }
            )

            if not resp.tool_calls:
                answer = (resp.content or "").strip()
                convo.append({"role": "assistant", "content": answer})
                return AgentResult(
                    success=bool(answer),
                    final_answer=answer,
                    steps=steps,
                    error=None if answer else "模型返回了空答案",
                    llm_calls=llm_call,
                    usage=usage,
                    new_messages=convo[start_len:],
                )

            convo.append(resp.to_assistant_message())
            thought = (resp.content or "").strip()
            results, clarification = await self._execute_batch(
                resp.tool_calls, llm_call, thought, allowed_tools, last_chance
            )
            for tc, step in zip(resp.tool_calls, results, strict=True):
                steps.append(step)
                convo.append({"role": "tool", "tool_call_id": tc.id, "content": step.output})
                await emit(step.to_dict())

            if clarification:
                answer = "开始分析前，请补充以下信息：\n" + "\n".join(
                    f"{index + 1}. {question}"
                    for index, question in enumerate(dict.fromkeys(clarification))
                )
                convo.append({"role": "assistant", "content": answer})
                return AgentResult(
                    success=True,
                    final_answer=answer,
                    steps=steps,
                    llm_calls=llm_call,
                    usage=usage,
                    new_messages=convo[start_len:],
                    needs_clarification=True,
                )

        # 正常情况下不会走到这里：最后一步不提供工具，模型必然直接作答
        return AgentResult(
            success=False,
            final_answer="",
            steps=steps,
            error=f"已达到最大步数限制 ({self.max_steps})",
            llm_calls=self.max_steps,
            usage=usage,
            new_messages=convo[start_len:],
        )

    async def _run_one(
        self, tc: ToolCall, step: int, thought: str, allowed_tools: set[str], last_chance: bool
    ) -> ToolStep:
        """执行一次工具调用；不允许的工具和非法参数都作为错误 observation 返回。"""
        if last_chance or tc.name not in allowed_tools:
            return _refused(step, tc, thought, f"本轮不允许调用工具: {tc.name}")
        args, err = _parse_arguments(tc)
        if err is not None:
            return _refused(step, tc, thought, err)
        outcome = await self._tools.invoke(tc.name, args or {})
        return ToolStep(
            step, tc.name, args or {}, outcome.ok, outcome.content, outcome.duration_ms, thought
        )

    async def _execute_batch(
        self,
        tool_calls: list[ToolCall],
        step: int,
        thought: str,
        allowed_tools: set[str],
        last_chance: bool,
    ) -> tuple[list[ToolStep], list[str]]:
        """执行模型一次返回的全部工具调用，按原顺序返回结果和需要用户澄清的问题。

        前置检查工具（确认口径、数据体检、固定指标）先按顺序执行；它们失败或要求澄清时，
        同批其余工具不执行，避免 SQL 抢在门禁之前运行。其余工具互不依赖，并发执行。
        """
        gate_calls = [tc for tc in tool_calls if tc.name in _GATE_TOOLS]
        gated: dict[str, ToolStep] = {}
        if sum(tc.name == "prepare_analysis" for tc in gate_calls) > 1:
            for tc in gate_calls:
                gated[tc.id] = _refused(
                    step, tc, thought, "同一批只能确认一个分析口径，请拆成有序步骤"
                )
        else:
            stopped = False
            # 先体检/确认，再执行指标
            for tc in sorted(
                gate_calls, key=lambda tc: tc.name in {"query_metric", "query_business_metric"}
            ):
                if stopped:
                    gated[tc.id] = _refused(step, tc, thought, "前置检查尚未通过，本批工具未执行")
                    continue
                item = await self._run_one(tc, step, thought, allowed_tools, last_chance)
                gated[tc.id] = item
                stopped = (
                    not item.ok or json.loads(item.output).get("status") == "needs_clarification"
                )

        clarification: list[str] = []
        for item in gated.values():
            if item.ok:
                payload = json.loads(item.output)
                if payload.get("status") == "needs_clarification":
                    clarification.extend(payload["questions"])
        blocked = bool(clarification) or any(not item.ok for item in gated.values())

        async def execute(tc: ToolCall) -> ToolStep:
            if tc.id in gated:
                return gated[tc.id]
            if blocked:
                return _refused(step, tc, thought, "分析口径尚未确认，本批工具未执行")
            return await self._run_one(tc, step, thought, allowed_tools, last_chance)

        results = await asyncio.gather(*(execute(tc) for tc in tool_calls))
        return list(results), clarification
