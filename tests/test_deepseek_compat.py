"""Exercise the real SDK/Agent through a DeepSeek-like mock, not a fake router."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.agent.react_agent import ReActAgent
from app.core.tools.builtin.calculator import CalculatorTool
from app.core.tools.registry import ToolRegistry
from app.infrastructure.llm.local_settings import LocalModelStore
from app.infrastructure.llm.model_router import ModelConfig, ModelRouter
from app.main import create_app

PAYLOAD = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key": "offline-valid-test-key",
}


def completion(*, tool=None, text=None, finish=None):
    message = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [{"id": "offline-call", "type": "function", "function": tool}]
    return httpx.Response(
        200,
        json={
            "id": "offline-result",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish or ("tool_calls" if tool else "stop"),
                }
            ],
        },
    )


def checking_client(tmp_path, monkeypatch, handler):
    app = create_app()
    store = LocalModelStore(tmp_path / "personal.json")
    app.state.local_model_store = store
    monkeypatch.setattr(
        "app.api.routes.model_settings.ModelRouter",
        lambda configs, **kwargs: ModelRouter(
            configs, transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    return TestClient(app), store


def test_deepseek_probe_disables_default_thinking_and_uses_compatible_tool_choice(
    tmp_path, monkeypatch
):
    calls = []

    def handler(request):
        value = json.loads(request.content)
        calls.append(value)
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer offline-valid-test-key"
        # DeepSeek rejects a forced named tool under its default thinking mode.
        thinking = value.get("thinking", {}).get("type", "enabled")
        if thinking == "enabled" and isinstance(value.get("tool_choice"), dict):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Named tool_choice is not supported in thinking mode",
                        "param": "tool_choice",
                    }
                },
            )
        if thinking == "enabled" or value.get("max_tokens", 0) < 128:
            return completion(finish="length")
        assert value["tool_choice"] == "auto"
        return completion(tool={"name": "connection_check", "arguments": "{}"})

    client, store = checking_client(tmp_path, monkeypatch, handler)
    result = client.post("/api/v1/model-settings/check", json=PAYLOAD)
    assert result.status_code == 200
    assert result.json() == {"connected": True, "tools_supported": True}
    assert len(calls) == 1 and calls[0]["thinking"] == {"type": "disabled"}
    assert store.read() is None  # Probe still never saves settings.


def test_real_agent_can_complete_a_two_call_tool_loop_without_thinking_transcript():
    calls = []

    def handler(request):
        value = json.loads(request.content)
        calls.append(value)
        assert value["thinking"] == {"type": "disabled"}
        if len(calls) == 1:
            assert value["tools"]
            return completion(tool={"name": "calculator", "arguments": '{"expression":"1+1"}'})
        assert value["messages"][-1]["role"] == "tool"
        assert value["messages"][-1]["tool_call_id"] == "offline-call"
        return completion(text="结果为 2。")

    async def run():
        router = ModelRouter(
            [
                ModelConfig(
                    model_id="deepseek-flash",
                    api_key="offline-key",
                    base_url="https://api.deepseek.com/v1",
                )
            ],
            transport=httpx.MockTransport(handler),
            max_retries=0,
        )
        registry = ToolRegistry()
        registry.register(CalculatorTool())
        try:
            result = await ReActAgent(router, registry, max_steps=3).run(
                [{"role": "user", "content": "计算 1+1"}]
            )
            assert result.success and result.steps[0].ok and "2" in result.final_answer
        finally:
            await router.aclose()

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize(
    "base_url,options,expected",
    [
        ("https://another.example.test/v1", {}, None),
        (
            "https://api.deepseek.com",
            {"extra_body": {"thinking": {"type": "enabled"}}},
            {"type": "enabled"},
        ),
        ("https://api.deepseek.com", {"reasoning_effort": "low"}, None),
    ],
)
def test_compatibility_does_not_add_other_vendor_fields_or_override_explicit_mode(
    base_url, options, expected
):
    def handler(request):
        assert json.loads(request.content).get("thinking") == expected
        return completion(text="offline")

    async def run():
        router = ModelRouter(
            [ModelConfig(model_id="offline-model", api_key="offline-key", base_url=base_url)],
            transport=httpx.MockTransport(handler),
            max_retries=0,
        )
        try:
            assert (
                await router.chat([{"role": "user", "content": "offline"}], **options)
            ).content == "offline"
        finally:
            await router.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "code,expected",
    [
        (400, "参数"),
        (401, "认证"),
        (402, "余额"),
        (403, "权限"),
        (404, "路径"),
        (429, "限流"),
        (503, "服务状态"),
    ],
)
def test_chat_probe_exposes_safe_http_category_without_provider_body_or_key(
    tmp_path, monkeypatch, code, expected
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code,
            json={
                "error": {
                    "message": "Authorization Bearer offline-valid-test-key",
                    "param": "tool_choice",
                    "code": "offline-valid-test-key",
                }
            },
        )

    client, _ = checking_client(tmp_path, monkeypatch, handler)
    response = client.post("/api/v1/model-settings/check", json=PAYLOAD)
    assert response.status_code == 502
    assert str(code) in response.json()["detail"] and expected in response.json()["detail"]
    assert PAYLOAD["api_key"] not in response.text
    assert len(calls) == 1


def test_timeout_has_its_own_safe_diagnostic_without_retry(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("offline-valid-test-key", request=request)

    client, _ = checking_client(tmp_path, monkeypatch, handler)
    response = client.post("/api/v1/model-settings/check", json=PAYLOAD)
    assert response.status_code == 502 and "超时" in response.json()["detail"]
    assert PAYLOAD["api_key"] not in response.text and len(calls) == 1
