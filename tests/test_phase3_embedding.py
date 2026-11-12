"""Embedding transport tests use MockTransport only; no Internet requests."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.knowledge.embedding import APIEmbedder, validate_vectors
from app.knowledge.types import EmbeddingError, EmbeddingProfile
from tests.conftest import run


def config(**updates):
    return Settings(
        _env_file=None,
        embedding_enabled=True,
        embedding_api_base="https://embedding.invalid/v1",
        embedding_api_key="test-secret",
        embedding_model="synthetic",
        embedding_dimensions=3,
        **updates,
    )


def response(count):
    return {
        "data": [{"index": i, "embedding": [1.0, float(i), 0]} for i in reversed(range(count))],
        "usage": {"total_tokens": count * 10},
    }


def test_transport_batching_reorders_and_counts_usage():
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200, json=response(len(body["input"])))

    async def scenario():
        client = APIEmbedder(config(embedding_batch_size=2), transport=httpx.MockTransport(handle))
        try:
            vectors = await client.embed(["a", "b", "c"])
            assert vectors == [[1, 0, 0], [1, 1, 0], [1, 0, 0]]
            assert client.usage == {"requests": 2, "input_tokens": 30, "texts": 3, "failures": 0}
        finally:
            await client.aclose()

    run(scenario())
    assert [len(item["input"]) for item in requests] == [2, 1]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_transient_http_errors(status):
    calls = []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(status, headers={"Retry-After": "0"})
            if len(calls) == 1
            else httpx.Response(200, json=response(1))
        )

    async def scenario():
        client = APIEmbedder(config(), transport=httpx.MockTransport(handle))
        try:
            assert await client.embed(["x"]) == [[1, 0, 0]]
        finally:
            await client.aclose()

    run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 302])
def test_no_retry_or_sensitive_response_body_on_permanent_errors(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="test-secret private document text",
            headers={"Location": "https://other.invalid"},
        )

    async def scenario():
        client = APIEmbedder(config(), transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(EmbeddingError) as error:
                await client.embed(["private document text"])
            assert str(error.value) == f"embedding_http_{status}"
        finally:
            await client.aclose()

    run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "vectors,count,dimensions,code",
    [
        ([], 1, 3, "count"),
        ([[1, 2]], 1, 3, "dimensions"),
        ([[1, float("nan"), 0]], 1, 3, "values"),
        ([[float("inf"), 0, 0]], 1, 3, "values"),
        ([[True, 0, 0]], 1, 3, "values"),
        ([["1", 0, 0]], 1, 3, "values"),
        ([[1e100, 0, 0]], 1, 3, "values"),
        ([[10**400, 0, 0]], 1, 3, "values"),
        ([[0, 0, 0]], 1, 3, "zero"),
    ],
)
def test_vector_validation(vectors, count, dimensions, code):
    with pytest.raises(EmbeddingError, match=code):
        validate_vectors(vectors, count, dimensions)


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 1, "embedding": [1, 0, 0]}],
        [{"index": 0, "embedding": [1, 0, 0]}, {"index": 0, "embedding": [1, 0, 0]}],
        [{"index": True, "embedding": [1, 0, 0]}],
        [{"embedding": [1, 0, 0]}],
    ],
)
def test_response_indices_are_complete_and_unique(data):
    async def scenario():
        client = APIEmbedder(
            config(),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": data})),
        )
        try:
            with pytest.raises(EmbeddingError, match="response_invalid"):
                await client.embed(["x"])
        finally:
            await client.aclose()

    run(scenario())


def test_disabled_missing_or_unsafe_configuration():
    for settings in [Settings(_env_file=None), Settings(_env_file=None, embedding_enabled=True)]:
        with pytest.raises(EmbeddingError):
            APIEmbedder(settings)
    with pytest.raises(EmbeddingError, match="https"):
        APIEmbedder(config().model_copy(update={"embedding_api_base": "http://remote.invalid/v1"}))
    assert "test-secret" not in repr(config())


def test_profile_separates_providers_models_dimensions_and_revisions():
    base = EmbeddingProfile.create("https://a.invalid", "m", 3, "v1")
    variants = [
        ("https://b.invalid", "m", 3, "v1"),
        ("https://a.invalid", "n", 3, "v1"),
        ("https://a.invalid", "m", 4, "v1"),
        ("https://a.invalid", "m", 3, "v2"),
    ]
    assert all(EmbeddingProfile.create(*args).revision != base.revision for args in variants)


def test_bounded_concurrency_and_cancellation():
    async def scenario():
        active, peak = 0, 0

        async def handle(request):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(0.02)
                return httpx.Response(200, json=response(1))
            finally:
                active -= 1

        client = APIEmbedder(config(embedding_concurrency=2), transport=httpx.MockTransport(handle))
        try:
            await asyncio.gather(*(client.embed([str(i)]) for i in range(8)))
            assert peak == 2
            task = asyncio.create_task(client.embed(["cancel"]))
            await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await client.aclose()

    run(scenario())
