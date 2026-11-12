"""Full stage accounting, retries and offline isolation. All network is mocked."""

import asyncio
import json
import os
import socket

import httpx
import pytest

from app.config import Settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.infrastructure.llm.metering import TrackedLLM, capture_usage
from app.infrastructure.llm.model_router import LLMResponse, ModelConfig, ModelRouter
from app.knowledge.embedding import APIEmbedder
from app.models.enums import AgentMode
from tests.conftest import ScriptedLLM, reply, run, tool_call


def test_planner_analysis_and_reflection_all_count(registry):
    review = {
        "passed": True,
        "quality_score": 95,
        "checks": [
            {"claim": "结果1", "status": "supported", "evidence_ids": ["E1"], "reason": "SQL 返回1"}
        ],
    }
    script = ScriptedLLM(
        [
            reply('{"steps":["查询结果"]}'),
            reply("", tool_call("run_sql", sql="SELECT 1")),
            reply("结果1"),
            reply(json.dumps(review)),
        ]
    )
    result = run(
        AgentOrchestrator(script, registry, InMemorySessionStore(), system_prompt="SYSTEM").run(
            "q", mode=AgentMode.PLAN_EXECUTE
        )
    )
    assert result.llm_calls == 4 and result.execution_llm_calls == 2
    assert result.usage["total_tokens"] == 40
    stages = result.usage_breakdown["stages"]
    assert {key: value["logical_calls"] for key, value in stages.items()} == {
        "planner": 1,
        "analysis": 2,
        "reflection": 1,
    }
    assert result.usage_breakdown["http_requests"] == 0  # A scripted model is not an HTTP request.


def test_concurrent_conversations_never_share_usage(registry):
    class Model:
        async def chat(self, messages, **kwargs):
            value = int(messages[-1]["content"])
            await asyncio.sleep(0.01)
            return LLMResponse(
                content="answer",
                usage={"prompt_tokens": value, "completion_tokens": 1, "total_tokens": value + 1},
            )

    async def scenario():
        instance = AgentOrchestrator(
            Model(), registry, InMemorySessionStore(), system_prompt="S", enable_reflection=False
        )
        a, b = await asyncio.gather(instance.run("10"), instance.run("20"))
        assert a.usage["total_tokens"] == 11 and b.usage["total_tokens"] == 21
        assert a.llm_calls == b.llm_calls == 1

    run(scenario())


def test_missing_usage_is_unknown_not_zero_cost():
    async def scenario():
        with capture_usage() as meter:
            await TrackedLLM(ScriptedLLM([LLMResponse(content="a")]), "planner").chat([])
            summary = meter.summary()
        assert not summary["usage_complete"] and summary["chat_usage"] == {}
        assert summary["stages"]["planner"]["missing_usage_calls"] == 1

    run(scenario())


def test_sdk_retries_count_physical_requests_and_keep_cache_tokens():
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(500, json={"error": {"message": "retry", "type": "server_error"}})
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "fake",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                    "prompt_cache_hit_tokens": 8,
                    "prompt_cache_miss_tokens": 4,
                },
            },
        )

    async def scenario():
        router = ModelRouter(
            [ModelConfig("fake", "fake-secret", "https://model.invalid/v1")],
            transport=httpx.MockTransport(respond),
            max_retries=1,
        )
        try:
            with capture_usage() as meter:
                await TrackedLLM(router, "reflection").chat(
                    [{"role": "user", "content": "synthetic"}]
                )
                summary = meter.summary()
            assert summary["http_requests"] == 2 and summary["llm_calls"] == 1
            assert not summary[
                "usage_complete"
            ]  # Failed/retried attempts may have unknown provider usage.
            assert summary["chat_usage"]["prompt_cache_hit_tokens"] == 8
            assert summary["chat_usage"]["total_tokens"] == 15
        finally:
            await router.aclose()

    run(scenario())
    assert "fake-secret" not in repr(ModelConfig("fake", "fake-secret"))


def test_query_embedding_is_separate_from_chat_tokens():
    async def scenario():
        settings = Settings(
            _env_file=None,
            embedding_enabled=True,
            embedding_api_base="https://embedding.invalid/v1",
            embedding_api_key="fake",
            embedding_model="synthetic",
            embedding_dimensions=2,
        )
        provider = APIEmbedder(
            settings,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "data": [{"index": 0, "embedding": [1.0, 0.0]}],
                        "usage": {"total_tokens": 7},
                    },
                )
            ),
        )
        try:
            with capture_usage() as meter:
                await provider.embed(["synthetic"])
                summary = meter.summary()
            assert summary["stages"]["embedding"]["http_requests"] == 1
            assert summary["stages"]["embedding"]["usage"]["input_tokens"] == 7
            assert summary["chat_usage"] == {} and summary["llm_calls"] == 0
        finally:
            await provider.aclose()

    run(scenario())


def test_ordinary_tests_disable_external_models_without_editing_user_configuration():
    assert os.environ["EMBEDDING_ENABLED"] == "false"
    assert os.environ["OPENAI_API_KEY"] == ""
    with pytest.raises(AssertionError, match="external"):
        socket.getaddrinfo("api.deepseek.com", 443)


@pytest.mark.parametrize("stream", [False, True])
def test_http_and_sse_keep_all_stages_and_legacy_execution_count(registry, stream):
    from fastapi.testclient import TestClient

    from app.main import create_app

    review = {
        "passed": True,
        "quality_score": 95,
        "checks": [
            {"claim": "结果1", "status": "supported", "evidence_ids": ["E1"], "reason": "查询返回1"}
        ],
    }
    script = ScriptedLLM(
        [
            reply('{"steps":["查询"]}'),
            reply("", tool_call("run_sql", sql="SELECT 1")),
            reply("结果1"),
            reply(json.dumps(review)),
        ]
    )
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = AgentOrchestrator(
            script, registry, InMemorySessionStore(), system_prompt="SYSTEM"
        )
        body = {"question": "结果是什么", "mode": "plan_execute"}
        if stream:
            with client.stream("POST", "/api/v1/chat/stream", json=body) as response:
                events = [
                    json.loads(line[6:])
                    for line in response.iter_lines()
                    if line.startswith("data: ")
                ]
            result = events[-1]
        else:
            result = client.post("/api/v1/chat", json=body).json()
        assert result["llm_calls"] == 4 and result["execution_llm_calls"] == 2
        assert result["usage"]["total_tokens"] == 40
        assert result["usage_breakdown"]["stages"]["planner"]["logical_calls"] == 1
        assert result["usage_breakdown"]["stages"]["reflection"]["logical_calls"] == 1
        assert not result["usage_breakdown"]["usage_complete"]  # Script only reports total_tokens.


def test_provider_error_body_and_api_key_never_enter_router_logs():
    import io

    from loguru import logger

    output = io.StringIO()
    sink = logger.add(output, format="{message}", diagnose=True)

    async def scenario():
        router = ModelRouter(
            [ModelConfig("fake", "fake-secret-never-log", "https://model.invalid/v1")],
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401,
                    json={
                        "error": {
                            "message": "private-body-never-log",
                            "type": "authentication_error",
                        }
                    },
                )
            ),
            max_retries=0,
        )
        try:
            with capture_usage() as meter:
                with pytest.raises(RuntimeError) as caught:
                    await TrackedLLM(router, "analysis").chat([])
                assert caught.value.__suppress_context__
                summary = meter.summary()
            assert not summary["usage_complete"] and summary["stages"]["analysis"]["failures"] == 1
        finally:
            await router.aclose()

    try:
        run(scenario())
    finally:
        logger.remove(sink)
    assert "private-body-never-log" not in output.getvalue()
    assert "fake-secret-never-log" not in output.getvalue()
