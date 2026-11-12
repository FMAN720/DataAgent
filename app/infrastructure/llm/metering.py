"""Request-scoped metering; task-local context keeps concurrent conversations separate."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_meter: ContextVar[UsageMeter | None] = ContextVar("agent_usage", default=None)
_phase: ContextVar[str | None] = ContextVar("agent_phase", default=None)
STAGES = ("planner", "analysis", "revision", "reflection", "embedding")
TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "reasoning_tokens",
)


@dataclass
class UsageMeter:
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def stage(self, name):
        if name not in self.stages:
            self.stages[name] = {
                "logical_calls": 0,
                "http_requests": 0,
                "failures": 0,
                "missing_usage_calls": 0,
                "duration_ms": 0,
                "usage": {},
            }
        return self.stages[name]

    def tokens(self, name, usage):
        item = self.stage(name)
        if not isinstance(usage, dict):
            item["missing_usage_calls"] += 1
            return
        if any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            item["missing_usage_calls"] += 1
        for key in TOKEN_FIELDS:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                item["usage"][key] = item["usage"].get(key, 0) + value

    def summary(self):
        import copy

        stages = copy.deepcopy(self.stages)
        chat = [item for name, item in stages.items() if name != "embedding"]
        usage = {}
        for item in chat:
            for key, value in item["usage"].items():
                usage[key] = usage.get(key, 0) + value
        return {
            "scope": "all_chat_stages; embedding reported separately",
            "stages": stages,
            "llm_calls": sum(item["logical_calls"] for item in chat),
            "chat_usage": usage,
            "http_requests": sum(item["http_requests"] for item in stages.values()),
            "usage_complete": all(
                item["missing_usage_calls"] == 0
                and item["failures"] == 0
                and item["http_requests"] <= item["logical_calls"]
                for item in stages.values()
            ),
        }


@contextmanager
def capture_usage():
    meter = UsageMeter()
    token = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(token)


@contextmanager
def usage_phase(name):
    token = _phase.set(name)
    try:
        yield
    finally:
        _phase.reset(token)


async def chat_request_hook(request):
    meter = _meter.get()
    if meter:
        meter.stage(_phase.get() or "analysis")["http_requests"] += 1


def embedding_event(*, request=False, usage=None, failed=False, logical=False, duration_ms=0):
    meter = _meter.get()
    if meter is None:
        return
    item = meter.stage("embedding")
    if logical:
        item["logical_calls"] += 1
    item["duration_ms"] += duration_ms
    if request:
        item["http_requests"] += 1
    if failed:
        item["failures"] += 1
    if usage is not None:
        if type(usage) is int and usage >= 0:
            item["usage"]["input_tokens"] = item["usage"].get("input_tokens", 0) + usage
        else:
            item["missing_usage_calls"] += 1


class TrackedLLM:
    def __init__(self, llm, stage):
        self.llm, self.name = llm, stage

    async def chat(self, messages, **kwargs):
        meter = _meter.get()
        if meter is None:
            return await self.llm.chat(messages, **kwargs)
        name = _phase.get() or self.name
        item, started = meter.stage(name), time.perf_counter()
        item["logical_calls"] += 1
        with usage_phase(name):
            try:
                response = await self.llm.chat(messages, **kwargs)
                meter.tokens(name, response.usage)
                return response
            except BaseException:
                item["failures"] += 1
                raise
            finally:
                item["duration_ms"] += int((time.perf_counter() - started) * 1000)
