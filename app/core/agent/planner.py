"""
规划器：复杂问题先拆分析步骤，再交给 ReAct 执行（Plan-then-ReAct）。

原先的实现是「计划里每一步直接调用工具」，但计划阶段并不知道表结构，
生成的工具参数基本不可用。这里改为：计划只产出自然语言步骤，
作为额外的 system 指引注入 ReAct；执行中发现计划不对时，模型可以自行调整，
相当于把「重规划」交给了 ReAct 循环本身。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from loguru import logger

from app.infrastructure.llm.model_router import LLMResponse

PLAN_SYSTEM_PROMPT = """你是数据分析规划专家。把用户的问题拆成 2~6 个有序的分析步骤，
每一步说明要获取或计算什么。不要写 SQL，不要假设具体表名。
只输出 JSON：{"steps": ["步骤1", "步骤2", ...]}
如果问题足够简单，一步就能回答，输出 {"steps": []}。
对比较、归因和下钻问题，步骤应包含：确认指标/来源/单位/两个周期；查询并核对整体与分项；
沿一个维度定位变化，必要时在同一父级继续下钻；查找同期业务资料；区分事实、资料解释和待验证假设。
不能假定品类明细金额等于订单净销售额，不能将不同维度贡献相加，也不能将相关性写成因果。"""


class PlannerLLM(Protocol):
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse: ...


@dataclass
class Plan:
    steps: list[str]

    def render(self) -> str:
        lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(self.steps))
        return f"## 分析计划（可根据实际数据调整）\n{lines}"


class PlannerAgent:
    def __init__(self, llm: PlannerLLM, *, model: str | None = None) -> None:
        self._llm = llm
        self.model = model

    async def plan(self, question: str, context: str = "") -> Plan:
        """生成计划；失败时返回空计划，退化为纯 ReAct。"""
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\n问题：{question}".strip()},
        ]
        try:
            resp = await self._llm.chat(
                messages,
                model_preference=self.model,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            text = resp.content.strip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{[\s\S]*\}", text)
                data = json.loads(m.group(0)) if m else {}
            steps = [str(s).strip() for s in data.get("steps", []) if str(s).strip()]
            return Plan(steps=steps[:6])
        except Exception as exc:  # noqa: BLE001
            logger.warning("生成计划失败，退化为纯 ReAct: {}", exc)
            return Plan(steps=[])
