"""OpenAI-compatible /embeddings transport using existing httpx; opt in only."""

from __future__ import annotations

import asyncio
import math
import time
from numbers import Real

import httpx

from app.config import Settings
from app.infrastructure.llm.metering import embedding_event
from app.knowledge.types import EmbeddingError, EmbeddingProfile


def validate_vectors(vectors, count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != count:
        raise EmbeddingError("embedding_count_mismatch")
    output = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise EmbeddingError("embedding_dimensions_mismatch")
        if any(
            isinstance(v, bool)
            or not isinstance(v, Real)
            # 不能先转 float：超大整数会抛 OverflowError，而不是被判为非法向量
            or abs(v) > 3.4028234663852886e38  # type: ignore[operator]
            or not math.isfinite(v)
            for v in vector
        ):
            raise EmbeddingError("embedding_values_invalid")
        converted = [float(v) for v in vector]
        if not any(converted):
            raise EmbeddingError("embedding_zero_vector")
        output.append(converted)
    return output


class APIEmbedder:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.embedding_enabled:
            raise EmbeddingError("embedding_disabled")
        if not (
            settings.embedding_api_key
            and settings.embedding_api_base
            and settings.embedding_model
            and settings.embedding_dimensions
        ):
            raise EmbeddingError("embedding_configuration_incomplete")
        try:
            url = httpx.URL(settings.embedding_api_base)
        except (httpx.InvalidURL, ValueError):
            raise EmbeddingError("embedding_url_invalid") from None
        if (
            url.scheme not in ("https", "http")
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise EmbeddingError("embedding_url_invalid")
        if url.scheme == "http" and url.host not in ("127.0.0.1", "localhost", "::1"):
            raise EmbeddingError("embedding_https_required")
        self.profile = EmbeddingProfile.create(
            str(url),
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_revision,
        )
        self._batch_size = settings.embedding_batch_size
        self._retries = settings.embedding_max_retries
        self._timeout = settings.embedding_timeout_seconds
        self._semaphore = asyncio.Semaphore(settings.embedding_concurrency)
        self._client = httpx.AsyncClient(
            base_url=str(url).rstrip("/") + "/",
            timeout=self._timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": "Bearer " + settings.embedding_api_key},
        )
        self.usage = {"requests": 0, "input_tokens": 0, "texts": 0, "failures": 0}

    async def aclose(self):
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise EmbeddingError("embedding_input_empty")
        output = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            output.extend(await self._request(batch))
        return output

    async def _request(self, texts: list[str]) -> list[list[float]]:
        started = time.perf_counter()
        embedding_event(logical=True)
        try:
            return await self._request_attempts(texts)
        finally:
            embedding_event(duration_ms=int((time.perf_counter() - started) * 1000))

    async def _request_attempts(self, texts: list[str]) -> list[list[float]]:
        for attempt in range(self._retries + 1):
            retry_after = 0.0
            try:
                # Also bound time spent waiting for another request's semaphore.
                async with asyncio.timeout(self._timeout):
                    async with self._semaphore:
                        self.usage["requests"] += 1
                        embedding_event(request=True)
                        response = await self._client.post(
                            "embeddings",
                            json={
                                "model": self.profile.model,
                                "input": texts,
                                "encoding_format": "float",
                            },
                        )
                if response.status_code == 429 or response.status_code >= 500:
                    try:
                        retry_after = min(
                            10, max(0, float(response.headers.get("Retry-After", "0")))
                        )
                    except ValueError:
                        pass
                    raise EmbeddingError(f"embedding_http_{response.status_code}", retryable=True)
                if response.status_code != 200:
                    raise EmbeddingError(f"embedding_http_{response.status_code}")
                try:
                    payload = response.json()
                    data = payload["data"]
                    if not isinstance(data, list) or any(
                        type(item["index"]) is not int for item in data
                    ):
                        raise ValueError()
                    by_index = {item["index"]: item["embedding"] for item in data}
                    if len(data) != len(texts) or set(by_index) != set(range(len(texts))):
                        raise ValueError()
                    result = validate_vectors(
                        [by_index[i] for i in range(len(texts))],
                        len(texts),
                        self.profile.dimensions,
                    )
                except EmbeddingError:
                    raise
                except (KeyError, TypeError, ValueError):
                    raise EmbeddingError("embedding_response_invalid") from None
                usage = payload.get("usage") or {}
                tokens = (
                    usage.get("total_tokens", usage.get("prompt_tokens", 0))
                    if isinstance(usage, dict)
                    else 0
                )
                if type(tokens) is int and tokens >= 0:
                    self.usage["input_tokens"] += tokens
                embedding_event(
                    usage=tokens
                    if isinstance(usage, dict)
                    and any(key in usage for key in ("total_tokens", "prompt_tokens"))
                    else False
                )
                self.usage["texts"] += len(texts)
                return result
            except (httpx.TransportError, TimeoutError):
                error = EmbeddingError("embedding_transport_error", retryable=True)
            except EmbeddingError as exc:
                error = exc
            if not error.retryable or attempt == self._retries:
                self.usage["failures"] += 1
                embedding_event(failed=True)
                raise error from None
            await asyncio.sleep(max(retry_after, min(2, 0.25 * 2**attempt)))
        raise AssertionError("unreachable")
