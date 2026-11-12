"""多模型路由器：优先级调度、加权选择与自动降级。"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger
from openai import APIError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from app.infrastructure.llm.circuit_breaker import CircuitBreaker
from app.infrastructure.llm.errors import ModelRequestError
from app.infrastructure.llm.metering import chat_request_hook
from app.infrastructure.llm.types import ModelProvider


class ToolCall(BaseModel):
    """模型发起的一次工具调用；arguments 是模型生成的原始 JSON 字符串，可能不合法。"""

    id: str
    name: str
    arguments: str = "{}"


class LLMResponse(BaseModel):
    """统一 LLM 响应结构。"""

    content: str = ""
    model_id: str = ""
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    def to_assistant_message(self) -> dict[str, Any]:
        """转成可追加回 messages 的 assistant 消息（含 tool_calls）。"""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]
        return msg


@dataclass
class ModelConfig:
    """单路模型配置。"""

    model_id: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    provider: ModelProvider = ModelProvider.OPENAI
    priority: int = 0
    weight: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


class ModelRouter:
    """多模型路由器：支持优先级调度、负载均衡（同优先级加权随机）、自动降级。"""

    def __init__(
        self,
        model_configs: list[ModelConfig],
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not model_configs:
            raise ValueError("model_configs 不能为空")

        self._configs = sorted(model_configs, key=lambda c: c.priority)
        self._breakers: dict[str, CircuitBreaker] = {}
        for cfg in self._configs:
            self._breakers[cfg.model_id] = CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                name=f"llm:{cfg.model_id}",
            )
        self._clients: dict[str, AsyncOpenAI] = {}
        for cfg in self._configs:
            kwargs: dict[str, Any] = {"api_key": cfg.api_key}
            if cfg.base_url:
                kwargs["base_url"] = cfg.base_url
            self._clients[cfg.model_id] = AsyncOpenAI(
                **kwargs,
                max_retries=max_retries,
                timeout=timeout_seconds,
                # openai 的类型标注指向 httpx2，运行时同样接受 httpx.AsyncClient
                http_client=httpx.AsyncClient(  # type: ignore[arg-type]
                    transport=transport,
                    trust_env=False,
                    event_hooks={"request": [chat_request_hook]},
                ),
            )

    async def aclose(self) -> None:
        await asyncio.gather(*(client.close() for client in self._clients.values()))

    def _select_candidates(
        self,
        model_preference: str | None,
    ) -> list[ModelConfig]:
        """按优先级分组，在同优先级内按权重随机排序，形成候选列表。"""
        if model_preference:
            exact = [c for c in self._configs if c.model_id == model_preference]
            if exact:
                return exact

        by_prio: dict[int, list[ModelConfig]] = {}
        for cfg in self._configs:
            by_prio.setdefault(cfg.priority, []).append(cfg)

        ordered: list[ModelConfig] = []
        for prio in sorted(by_prio.keys()):
            group = by_prio[prio]
            # 同优先级内按权重做随机排序（权重越大越容易被排到前面）
            scored = [(cfg, random.random() ** (1.0 / max(cfg.weight, 0.01))) for cfg in group]
            scored.sort(key=lambda x: -x[1])
            ordered.extend(cfg for cfg, _ in scored)
        return ordered or list(self._configs)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model_preference: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """智能路由到合适模型；失败时按候选顺序自动降级。"""
        candidates = self._select_candidates(model_preference)
        last_error: Exception | None = None

        for cfg in candidates:
            breaker = self._breakers[cfg.model_id]
            try:
                return await breaker.call(
                    self._try_model,
                    cfg.model_id,
                    messages,
                    **kwargs,
                )
            except RuntimeError as exc:
                # 熔断打开
                last_error = exc
                logger.warning("模型 [{}] 被熔断跳过: {}", cfg.model_id, type(exc).__name__)
            except (TimeoutError, APIError, RateLimitError) as exc:
                last_error = exc
                logger.warning("模型 [{}] 调用失败，尝试降级: {}", cfg.model_id, type(exc).__name__)
            except Exception as exc:
                last_error = exc
                logger.warning("模型 [{}] 调用异常: {}", cfg.model_id, type(exc).__name__)

        raise ModelRequestError.from_exception(last_error) from None

    async def _try_model(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        """尝试调用指定模型（经熔断器包装，不在此处重复熔断逻辑）。"""
        client = self._clients[model_id]
        temperature = kwargs.pop("temperature", 0.7)
        max_tokens = kwargs.pop("max_tokens", None)

        params: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        params.update(kwargs)
        # DeepSeek now defaults to thinking. This Agent's tool loop does not use
        # thinking transcripts; explicitly select its supported non-thinking mode.
        # Do not forward vendor-specific fields to other compatible providers, or
        # override an advanced caller's explicit mode.
        if urlsplit(str(client.base_url)).hostname == "api.deepseek.com":
            extra = dict(params.get("extra_body") or {})
            if (
                "thinking" not in extra
                and "reasoning_effort" not in extra
                and params.get("reasoning_effort") is None
            ):
                extra["thinking"] = {"type": "disabled"}
                params["extra_body"] = extra

        try:
            resp = await client.chat.completions.create(**params)
        except Exception:
            logger.warning("OpenAI 兼容接口调用失败 model_id={}", model_id)
            raise

        choice = resp.choices[0] if resp.choices else None
        content = (choice.message.content or "") if choice else ""
        tool_calls: list[ToolCall] = []
        if choice and choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                fn = getattr(tc, "function", None)
                if fn is None:
                    continue
                tool_calls.append(ToolCall(id=tc.id, name=fn.name, arguments=fn.arguments or "{}"))
        usage = None
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
            for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
                value = getattr(resp.usage, key, None)
                if type(value) is int and value >= 0:
                    usage[key] = value
            reasoning = getattr(
                getattr(resp.usage, "completion_tokens_details", None), "reasoning_tokens", None
            )
            if type(reasoning) is int and reasoning >= 0:
                usage["reasoning_tokens"] = reasoning

        return LLMResponse(
            content=content,
            model_id=model_id,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason if choice else None,
            usage=usage,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )
