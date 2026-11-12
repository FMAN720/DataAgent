"""Persistent pre-send limits for explicitly authorized synthetic evaluation only."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx


def replace_with_retry(temporary, target, attempts=20):
    """Windows 上杀毒软件或索引服务可能短暂占用目标文件，原子替换会报拒绝访问；稍等重试。"""
    for attempt in range(attempts):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


class EvaluationBudgetExceeded(RuntimeError):
    pass


class EvaluationLedger:
    def __init__(
        self,
        path: Path,
        fingerprint: str,
        *,
        max_cny=30,
        max_chat_requests=600,
        max_embedding_requests=250,
        max_characters=8_000_000,
    ):
        self.path, self.lock = path, asyncio.Lock()
        limits = dict(
            max_cny=max_cny,
            max_chat_requests=max_chat_requests,
            max_embedding_requests=max_embedding_requests,
            max_characters=max_characters,
            input_ceiling_cny_per_million=10,
            output_ceiling_cny_per_million=30,
        )
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if self.data["fingerprint"] != fingerprint or self.data["limits"] != limits:
                raise ValueError(
                    "Existing evaluation ledger configuration differs; do not reset authorization"
                )
        else:
            self.data = {
                "fingerprint": fingerprint,
                "limits": limits,
                "requests": [],
                "created_at": datetime.now(UTC).isoformat(),
            }
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_with_retry(temporary, self.path)

    async def reserve(self, kind, body):
        async with self.lock:
            rows, limits = self.data["requests"], self.data["limits"]
            if any(row.get("reservation_exceeded") for row in rows):
                raise EvaluationBudgetExceeded(
                    "Provider usage exceeded reservation; stop for review"
                )
            if sum(row["kind"] == kind for row in rows) >= limits["max_" + kind + "_requests"]:
                raise EvaluationBudgetExceeded("HTTP request budget exhausted")
            characters = len(body)
            if sum(row["characters"] for row in rows) + characters > limits["max_characters"]:
                raise EvaluationBudgetExceeded("Input character budget exhausted")
            # UTF-8 bytes plus protocol overhead conservatively bound text tokens.
            # These are authorization ceilings, not a claimed provider tariff.
            reservation = (
                (Decimal(len(body.encode("utf-8")) + 2048) * 10 + Decimal(2048) * 30) / 1_000_000
                if kind == "chat"
                else Decimal(0)
            )
            spent = sum(Decimal(row["ceiling_cny"]) for row in rows)
            if spent + reservation > Decimal(str(limits["max_cny"])):
                raise EvaluationBudgetExceeded("Conservative CNY budget exhausted")
            record = {
                "kind": kind,
                "characters": characters,
                "ceiling_cny": str(reservation),
                "status": "reserved",
                "usage": None,
            }
            rows.append(record)
            self.save()  # Crash before/after send keeps the reservation charged to the budget.
            return len(rows) - 1

    async def complete(self, index, response):
        async with self.lock:
            row = self.data["requests"][index]
            row["http_status"] = response.status_code
            try:
                usage = response.json().get("usage")
            except (ValueError, AttributeError):
                usage = None
            if isinstance(usage, dict):
                row["usage"] = {
                    key: value for key, value in usage.items() if type(value) is int and value >= 0
                }
                if row["kind"] == "chat" and all(
                    type(usage.get(key)) is int and usage[key] >= 0
                    for key in ("prompt_tokens", "completion_tokens")
                ):
                    actual = (
                        Decimal(usage["prompt_tokens"]) * 10
                        + Decimal(usage["completion_tokens"]) * 30
                    ) / 1_000_000
                    if actual > Decimal(row["ceiling_cny"]):
                        row["reservation_exceeded"] = True
                        row["ceiling_cny"] = str(actual)
                    else:
                        row["ceiling_cny"] = str(actual)
            row["status"] = "returned"
            self.save()


class BudgetedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport, ledger, kind, model, *, secrets=()):
        self.transport, self.ledger, self.kind, self.model = transport, ledger, kind, model
        self.secrets = tuple(value for value in secrets if value)
        self.blocked = False

    async def handle_async_request(self, request):
        try:
            return await self._handle(request)
        except EvaluationBudgetExceeded:
            self.blocked = True
            raise

    async def _handle(self, request):
        host, path = (
            ("api.deepseek.com", "/v1/chat/completions")
            if self.kind == "chat"
            else ("api.siliconflow.cn", "/v1/embeddings")
        )
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != host
            or request.url.path != path
            or request.url.query
        ):
            raise EvaluationBudgetExceeded("Destination outside approved evaluation")
        body = request.content.decode("utf-8")
        if len(body) > 100_000 or any(secret in body for secret in self.secrets):
            raise EvaluationBudgetExceeded("Request exceeds allowed content scope")
        payload = json.loads(body)
        if payload.get("model") != self.model:
            raise EvaluationBudgetExceeded("Unexpected model")
        if self.kind == "chat" and (
            payload.get("stream")
            or type(payload.get("max_tokens")) is not int
            or not 1 <= payload["max_tokens"] <= 2048
        ):
            raise EvaluationBudgetExceeded("Output must be explicitly bounded")
        index = await self.ledger.reserve(self.kind, body)
        response = await self.transport.handle_async_request(request)
        await response.aread()
        await self.ledger.complete(index, response)
        return response

    async def aclose(self):
        await self.transport.aclose()
