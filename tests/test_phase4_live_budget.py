"""The real-eval guard is tested with an in-memory HTTP transport only."""

import httpx
import pytest

from app.knowledge.types import EmbeddingError
from eval.phase3_live import BudgetTransport
from tests.conftest import run


def test_request_character_and_destination_guards():
    async def scenario():
        sent = []

        async def respond(request):
            sent.append(request)
            return httpx.Response(500)

        transport = BudgetTransport(httpx.MockTransport(respond), max_requests=2, max_characters=5)
        async with httpx.AsyncClient(transport=transport) as client:
            payload = {"model": "BAAI/bge-m3", "input": ["中文"]}
            for _ in range(2):
                await client.post("https://api.siliconflow.cn/v1/embeddings", json=payload)
            with pytest.raises(EmbeddingError, match="budget_exceeded"):
                await client.post("https://api.siliconflow.cn/v1/embeddings", json=payload)
            with pytest.raises(EmbeddingError, match="destination"):
                await client.post("https://example.com/v1/embeddings", json=payload)
            with pytest.raises(EmbeddingError, match="model"):
                await client.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    json={"model": "other", "input": ["x"]},
                )
        assert len(sent) == transport.requests == 2 and transport.characters == 4
        small = BudgetTransport(httpx.MockTransport(respond), max_characters=1)
        async with httpx.AsyncClient(transport=small) as client:
            with pytest.raises(EmbeddingError, match="budget_exceeded"):
                await client.post("https://api.siliconflow.cn/v1/embeddings", json=payload)
        assert small.requests == 0

    run(scenario())
