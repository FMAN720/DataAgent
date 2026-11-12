"""Redis 会话的配置、错误边界及 HTTP 行为；无需连接 Redis。"""

from __future__ import annotations

import builtins
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.redis_session import (
    RedisSessionStore,
    SessionStoreUnavailable,
    build_session_store,
)
from app.core.memory.session import InMemorySessionStore
from app.main import create_app
from tests.conftest import ScriptedLLM, reply, run


def fake_client():
    return SimpleNamespace(
        ping=AsyncMock(return_value=True),
        lrange=AsyncMock(return_value=[]),
        delete=AsyncMock(),
        aclose=AsyncMock(),
        pipeline=MagicMock(),
    )


def agent(sessions, registry, script=()):
    llm = ScriptedLLM(list(script))
    return AgentOrchestrator(
        llm,
        registry,
        sessions,
        system_prompt="SYSTEM",
        enable_reflection=False,
    ), llm


def test_default_memory_backend_does_not_import_redis(monkeypatch):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "redis" or name.startswith("redis."):
            raise AssertionError("memory must not import Redis")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    settings = Settings(_env_file=None, session_max_turns=3, session_ttl_seconds=9)
    store = build_session_store(settings)
    assert store.backend == "memory"
    assert store.max_turns == 3 and store.ttl_seconds == 9


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_backend": "unknown"},
        {"session_max_turns": 0},
        {"session_ttl_seconds": 0},
        {"redis_timeout_seconds": 0},
        {"redis_key_prefix": " "},
        {"redis_max_connections": 0},
    ],
)
def test_invalid_session_configuration_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


def test_redis_url_is_hidden_in_settings_repr():
    settings = Settings(_env_file=None, redis_url="redis://:test-secret@127.0.0.1:16379/0")
    assert "test-secret" not in repr(settings)


def test_redis_missing_dependency_has_actionable_error(monkeypatch):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name.startswith("redis."):
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(RuntimeError, match="requirements-redis.txt"):
        build_session_store(Settings(_env_file=None, session_backend="redis"))


def test_expired_memory_is_not_resurrected_on_append(monkeypatch):
    import app.core.memory.session as module

    now = [1.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def scenario():
        store = InMemorySessionStore(ttl_seconds=10)
        await store.append_turn("s", "old", "old answer")
        now[0] = 20.0
        await store.append_turn("s", "new", "new answer")
        assert await store.get_history("s") == [
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "new answer"},
        ]

    run(scenario())


def test_decodes_only_user_assistant_pairs_and_isolates_keys():
    client = fake_client()
    client.lrange.return_value = [json.dumps({"v": 1, "user": "华北？", "assistant": "结果"})]
    store = RedisSessionStore(client, max_turns=2)
    assert run(store.get_history("strange:*/会话")) == [
        {"role": "user", "content": "华北？"},
        {"role": "assistant", "content": "结果"},
    ]
    key = client.lrange.call_args.args[0]
    assert key.startswith("analyst:session:v1:") and "strange" not in key
    assert len(key.rsplit(":", 1)[1]) == 64


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"v":2,"user":"q","assistant":"a"}',
        '{"v":1,"user":null,"assistant":"a"}',
        '{"role":"system","content":"untrusted"}',
    ],
)
def test_corrupt_history_is_not_injected_or_silently_discarded(payload):
    client = fake_client()
    client.lrange.return_value = [payload]
    with pytest.raises(SessionStoreUnavailable):
        run(RedisSessionStore(client).get_history("s"))


@pytest.mark.parametrize("operation", ["get_history", "clear", "check_ready", "append_turn"])
def test_backend_errors_are_sanitized(operation):
    client = fake_client()
    failure = OSError("redis://:test-secret@host/0 private question")
    client.lrange.side_effect = client.delete.side_effect = client.ping.side_effect = failure
    client.pipeline.side_effect = failure
    store = RedisSessionStore(client)
    args = (
        ()
        if operation == "check_ready"
        else ("s", "q", "a")
        if operation == "append_turn"
        else ("s",)
    )
    with pytest.raises(SessionStoreUnavailable) as error:
        run(getattr(store, operation)(*args))
    assert "test-secret" not in str(error.value) and "private question" not in str(error.value)
    if operation == "append_turn":
        assert "重试可能再次调用模型" in str(error.value)


@pytest.mark.parametrize("path", ["/api/v1/chat", "/api/v1/chat/stream"])
def test_unavailable_redis_returns_503_before_model_call(registry, path):
    backend = fake_client()
    backend.ping.side_effect = backend.lrange.side_effect = OSError("secret")
    instance, llm = agent(RedisSessionStore(backend), registry)
    app = create_app()
    app.state.agent = instance
    with TestClient(app, raise_server_exceptions=True) as client:
        app.state.agent = instance
        response = client.post(path, json={"question": "q"})
        assert response.status_code == 503
        assert "Redis" in response.json()["detail"] and "secret" not in response.text
    assert not llm.calls


def test_sse_reports_failure_after_preflight_without_final_event(registry):
    backend = fake_client()
    backend.lrange.side_effect = OSError("secret")
    instance, llm = agent(RedisSessionStore(backend), registry)
    app = create_app()
    app.state.agent = instance
    with TestClient(app) as client:
        app.state.agent = instance
        response = client.post("/api/v1/chat/stream", json={"question": "q"})
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["start", "error"]
    assert "secret" not in response.text and not llm.calls


def test_failed_write_does_not_announce_success_or_retry_model(registry):
    backend = fake_client()
    backend.pipeline.side_effect = OSError("secret")
    instance, llm = agent(RedisSessionStore(backend), registry, [reply("synthetic answer")])
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = instance
        response = client.post("/api/v1/chat", json={"question": "q"})
    assert response.status_code == 503 and "重试可能再次调用模型" in response.json()["detail"]
    assert len(llm.calls) == 1


def test_health_distinguishes_session_failure_from_analysis_database(registry):
    backend = fake_client()
    backend.ping.side_effect = OSError("secret")
    instance, _ = agent(RedisSessionStore(backend), registry)
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = instance
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 503
        assert response.json()["analysis_database"] == "up"
        assert response.json()["session_store"] == "down"
        assert response.json()["session_backend"] == "redis"
        assert client.get("/api/v1/health").status_code == 200


def test_clear_session_only_removes_requested_history(registry):
    sessions = InMemorySessionStore()

    async def seed():
        await sessions.append_turn("target", "q", "a")
        await sessions.append_turn("other", "other q", "other a")

    run(seed())
    instance, _ = agent(sessions, registry)
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = instance
        assert client.delete("/api/v1/sessions/target").status_code == 204
    assert not run(sessions.get_history("target"))
    assert run(sessions.get_history("other"))


def test_agent_closes_connections_even_if_other_close_fails(registry):
    backend = fake_client()
    callback = AsyncMock(side_effect=RuntimeError("close failed"))
    instance = AgentOrchestrator(
        ScriptedLLM([]),
        registry,
        RedisSessionStore(backend),
        system_prompt="s",
        close_callback=callback,
    )
    with pytest.raises(RuntimeError):
        run(instance.aclose())
    backend.aclose.assert_awaited_once()
    callback.assert_awaited_once()
