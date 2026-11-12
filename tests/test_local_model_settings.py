"""Personal-model configuration: local persistence, no owner-Key fallback or live calls."""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.infrastructure.llm.local_settings import (
    LocalModelClient,
    LocalModelError,
    LocalModelStore,
    ModelProfile,
)
from app.infrastructure.llm.model_router import LLMResponse, ToolCall
from app.main import create_app


def profile(model="personal-model", key="offline-fixture-key"):
    return ModelProfile(
        base_url="https://models.example.test/v1", model=model, api_key=SecretStr(key)
    )


@pytest.fixture()
def local_client(tmp_path):
    app = create_app()
    store = LocalModelStore(tmp_path / "personal" / "model.json")
    app.state.local_model_store = store
    return TestClient(app), store


def test_persistence_is_private_and_public_metadata_never_returns_the_key(local_client):
    client, store = local_client
    assert client.get("/api/v1/model-settings").json()["configured"] is False
    data = {
        "base_url": "https://models.example.test/v1/",
        "model": "mine",
        "api_key": "offline-private-key",
    }
    saved = client.put("/api/v1/model-settings", json=data)
    assert saved.status_code == 200
    assert saved.json()["base_url"] == "https://models.example.test/v1"
    assert "offline-private-key" not in saved.text
    assert "api_key" not in saved.json()
    assert store.require().api_key.get_secret_value() == "offline-private-key"
    raw = store.path.read_text(encoding="utf-8")
    if os.name == "nt":
        assert "offline-private-key" not in raw
        assert "dpapi-v1:" in raw
    else:
        assert store.path.stat().st_mode & 0o077 == 0
    reloaded = LocalModelStore(store.path)
    assert reloaded.require().model == "mine"
    assert "offline-private-key" not in repr(reloaded.require())
    assert "offline-private-key" not in client.get("/api/v1/model-settings").text


def test_preserving_key_requires_same_endpoint_and_clear_does_not_fall_back(local_client):
    client, store = local_client
    store.save(profile())
    update = {"base_url": "https://models.example.test/v1", "model": "another-model"}
    assert client.put("/api/v1/model-settings", json=update).status_code == 200
    assert store.require().api_key.get_secret_value() == "offline-fixture-key"
    update["base_url"] = "https://another.example.test/v1"
    assert client.put("/api/v1/model-settings", json=update).status_code == 422
    assert store.require().base_url == "https://models.example.test/v1"
    assert client.delete("/api/v1/model-settings").json()["configured"] is False
    with pytest.raises(LocalModelError, match="自己的"):
        store.require()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:bad",
        "https://a.test/?key=secret",
        "https://name:password@a.test/v1",
        "https://a.test/#fragment",
    ],
)
def test_bad_settings_do_not_echo_secrets_or_replace_the_old_profile(local_client, url):
    client, store = local_client
    store.save(profile())
    response = client.put(
        "/api/v1/model-settings",
        json={"base_url": url, "model": "mine", "api_key": "offline-secret-not-to-echo"},
    )
    assert response.status_code == 422
    assert "offline-secret-not-to-echo" not in response.text
    assert store.require().model == "personal-model"


def test_cross_origin_settings_are_rejected(local_client):
    client, store = local_client
    assert (
        client.get(
            "/api/v1/model-settings", headers={"Origin": "https://elsewhere.test"}
        ).status_code
        == 403
    )
    assert store.read() is None


def test_nonlocal_settings_requests_are_rejected(local_client):
    client, _ = local_client

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app, client=("198.51.100.4", 4000)),
            base_url="http://testserver",
        ) as remote:
            assert (await remote.get("/api/v1/model-settings")).status_code == 403

    asyncio.run(check())


def test_docker_bridge_requires_opt_in_loopback_host_and_same_origin(local_client):
    client, _ = local_client

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app, client=("172.18.0.1", 4000)),
            base_url="http://127.0.0.1:8000",
        ) as bridge:
            assert (await bridge.get("/api/v1/model-settings")).status_code == 403
            client.app.state.local_model_settings_trust_proxy = True
            assert (await bridge.get("/api/v1/model-settings")).status_code == 200
            assert (
                await bridge.get("/api/v1/model-settings", headers={"Host": "public.test"})
            ).status_code == 403
            assert (
                await bridge.get("/api/v1/model-settings", headers={"Origin": "https://other.test"})
            ).status_code == 403
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app, client=("1.1.1.1", 4000)),
            base_url="http://127.0.0.1:8000",
        ) as public:
            assert (await public.get("/api/v1/model-settings")).status_code == 403

    asyncio.run(check())


def test_running_turn_uses_its_snapshot_and_always_closes_client(tmp_path, monkeypatch):
    store = LocalModelStore(tmp_path / "model.json")
    captured, closed = [], []

    class FakeRouter:
        def __init__(self, configs):
            self.config = configs[0]

        async def chat(self, messages, **kwargs):
            captured.append((self.config.model_id, self.config.api_key))
            return LLMResponse(content="offline", model_id=self.config.model_id)

        async def aclose(self):
            closed.append(self.config.model_id)

    monkeypatch.setattr("app.infrastructure.llm.local_settings.ModelRouter", FakeRouter)
    client = LocalModelClient(store)
    store.save(profile("first", "offline-first-key"))

    async def check():
        async with client.session():
            await client.chat([])
            store.save(profile("second", "offline-second-key"))
            await client.chat([])

            # A concurrent request gets the new profile, without changing this context.
            async def concurrent_turn():
                async with client.session():
                    await client.chat([])

            await asyncio.create_task(concurrent_turn())
            await client.chat([])
        with pytest.raises(LocalModelError):
            await client.chat([])

    asyncio.run(check())
    assert captured == [
        ("first", "offline-first-key"),
        ("first", "offline-first-key"),
        ("second", "offline-second-key"),
        ("first", "offline-first-key"),
    ]
    assert sorted(closed) == ["first", "second"]


def test_check_is_explicit_does_not_save_and_does_not_echo_provider_errors(
    local_client, monkeypatch
):
    client, store = local_client
    calls = []

    class FakeRouter:
        def __init__(self, configs, **kwargs):
            calls.append(configs[0])

        async def chat(self, messages, **kwargs):
            assert "connection_check" in messages[0]["content"]
            return LLMResponse(tool_calls=[ToolCall(id="offline", name="connection_check")])

        async def aclose(self):
            pass

    monkeypatch.setattr("app.api.routes.model_settings.ModelRouter", FakeRouter)
    payload = {
        "base_url": "https://models.example.test/v1",
        "model": "mine",
        "api_key": "offline-private-key",
    }
    assert client.put("/api/v1/model-settings", json=payload).status_code == 200
    assert not calls  # Save is local; it never sends a model request.
    client.delete("/api/v1/model-settings")
    checked = client.post("/api/v1/model-settings/check", json=payload)
    assert checked.json() == {"connected": True, "tools_supported": True}
    assert store.read() is None
    assert len(calls) == 1

    async def failed(self, messages, **kwargs):
        raise RuntimeError("Provider echoed offline-private-key")

    monkeypatch.setattr(FakeRouter, "chat", failed)
    error = client.post("/api/v1/model-settings/check", json=payload)
    assert error.status_code == 502
    assert "offline-private-key" not in error.text


def test_web_chat_and_stream_never_fall_back_to_developer_env_key(tmp_path, monkeypatch):
    settings = Settings(
        openai_api_key="offline-developer-key-must-not-be-used",
        analytics_db_url="sqlite:///" + (tmp_path / "demo.db").as_posix(),
        local_model_settings_path=tmp_path / "not-configured.json",
        documents_enabled=False,
        datasets_enabled=False,
        vision_enabled=False,
        embedding_enabled=False,
        analysis_history_enabled=False,
        session_backend="memory",
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        assert client.app.state.agent is not None  # Data tools remain assembled.
        for path in ["/api/v1/chat", "/api/v1/chat/stream"]:
            response = client.post(path, json={"question": "must not reach a model"})
            assert response.status_code == 428
            assert "自己的" in response.json()["detail"]
            assert "offline-developer-key" not in response.text


def test_http_chat_uses_saved_personal_profile_and_closes_its_client(tmp_path, monkeypatch):
    captured, closed = [], []

    class FakeRouter:
        def __init__(self, configs):
            captured.append(configs[0])

        async def chat(self, messages, **kwargs):
            return LLMResponse(content="离线个人模型回答", model_id=captured[-1].model_id)

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr("app.infrastructure.llm.local_settings.ModelRouter", FakeRouter)
    settings = Settings(
        openai_api_key="offline-owner-key",
        analytics_db_url="sqlite:///" + (tmp_path / "demo.db").as_posix(),
        local_model_settings_path=tmp_path / "personal.json",
        documents_enabled=False,
        datasets_enabled=False,
        vision_enabled=False,
        embedding_enabled=False,
        analysis_history_enabled=False,
        session_backend="memory",
        enable_reflection=False,
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        saved = client.put(
            "/api/v1/model-settings",
            json={
                "base_url": "https://offline.test/v1",
                "model": "personal-model",
                "api_key": "offline-personal-key",
            },
        )
        assert saved.status_code == 200 and not captured
        response = client.post("/api/v1/chat", json={"question": "离线集成检查"})
        assert response.status_code == 200
        assert response.json()["answer"] == "离线个人模型回答"
        assert captured[0].api_key == "offline-personal-key"
        assert captured[0].base_url == "https://offline.test/v1"
        assert captured[0].model_id == "personal-model"
        assert closed == [True]
        assert (
            "offline-personal-key" not in response.text and "offline-owner-key" not in response.text
        )


def test_corrupt_profile_can_be_replaced_without_reusing_its_key(local_client):
    client, store = local_client
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert client.get("/api/v1/model-settings").json()["configured"] is False
    response = client.put(
        "/api/v1/model-settings",
        json={
            "base_url": "https://models.example.test/v1",
            "model": "recovered",
            "api_key": "offline-new-key",
        },
    )
    assert response.status_code == 200
    assert store.require().model == "recovered"
