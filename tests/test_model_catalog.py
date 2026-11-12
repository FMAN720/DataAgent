"""Provider discovery, purpose isolation and live-operation snapshots, without provider calls."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.infrastructure.llm import provider_api
from app.infrastructure.llm.local_runtime import LocalRuntimeModels
from app.infrastructure.llm.local_settings import LocalModelStore, ModelProfile
from app.knowledge.service import KnowledgeService
from app.knowledge.types import EmbeddingProfile
from app.knowledge.worker import IndexWorker
from app.main import create_app
from app.vision.service import VisionService


def profile(name="offline-model", *, key="offline-key", dimensions=None, enabled=True):
    return ModelProfile(
        base_url="https://provider.example.test/v1",
        model=name,
        api_key=SecretStr(key),
        dimensions=dimensions,
        enabled=enabled,
    )


@pytest.fixture()
def client_store(tmp_path):
    app = create_app()
    store = LocalModelStore(tmp_path / "model.json")
    app.state.local_model_store = store
    app.state.local_runtime_models = LocalRuntimeModels(store, Settings(_env_file=None))
    app.state.vision_service = object()
    return TestClient(app), store


def mock_provider(monkeypatch, handler):
    monkeypatch.setattr(
        provider_api,
        "api_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
        ),
    )


def test_discovery_only_lists_provider_models_without_saving_or_inference(
    client_store, monkeypatch
):
    client, store = client_store
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer offline-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "z"},
                    {"id": "A"},
                    {"id": "z"},
                    {"id": "offline-key"},
                    {"id": "bad\nmodel"},
                    None,
                ]
            },
        )

    mock_provider(monkeypatch, handler)
    response = client.post(
        "/api/v1/model-settings/models",
        json={"base_url": "https://provider.example.test/v1", "api_key": "offline-key"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["models"]] == ["A", "z"]
    assert "offline-key" not in response.text and len(calls) == 1
    assert not store.path.exists()


@pytest.mark.parametrize("code", [301, 401, 403, 404, 429, 500])
def test_discovery_provider_failures_are_sanitized_and_not_retried(client_store, monkeypatch, code):
    client, _ = client_store
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code,
            text="Authorization: Bearer offline-secret",
            headers={"Location": "https://other.test/v1/models"},
        )

    mock_provider(monkeypatch, handler)
    response = client.post(
        "/api/v1/model-settings/models",
        json={"base_url": "https://provider.example.test/v1", "api_key": "offline-secret"},
    )
    assert response.status_code == 502 and "offline-secret" not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [{"data": []}, {"data": [{"no_id": "x"}]}])
def test_empty_catalog_is_a_valid_empty_list(client_store, monkeypatch, payload):
    client, _ = client_store
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json=payload))
    response = client.post(
        "/api/v1/model-settings/models",
        json={"base_url": "https://provider.example.test/v1", "api_key": "offline-key"},
    )
    assert response.status_code == 200 and response.json()["models"] == []


def test_catalog_rejects_html_and_oversized_bodies(client_store, monkeypatch):
    client, _ = client_store
    mock_provider(monkeypatch, lambda request: httpx.Response(200, text="<html>not an API</html>"))
    assert (
        client.post(
            "/api/v1/model-settings/models",
            json={"base_url": "https://provider.example.test/v1", "api_key": "offline-key"},
        ).status_code
        == 502
    )
    assert (
        client.post(
            "/api/v1/model-settings/models",
            content=b"x" * 65537,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_purposes_never_borrow_keys_and_clear_only_the_selected_profile(client_store, monkeypatch):
    client, store = client_store
    store.save(profile(key="offline-chat"))
    no_key = client.post(
        "/api/v1/model-settings/models?purpose=vision",
        json={"base_url": "https://provider.example.test/v1"},
    )
    assert no_key.status_code == 422
    for purpose, key in [
        ("vision", "offline-vision-secret-key"),
        ("embedding", "offline-vector-secret-key"),
    ]:
        body = {
            "base_url": "https://provider.example.test/v1",
            "model": "offline-" + purpose,
            "api_key": key,
            "enabled": True,
        }
        if purpose == "embedding":
            body["dimensions"] = 3
        saved = client.put("/api/v1/model-settings?purpose=" + purpose, json=body)
        assert saved.status_code == 200 and key not in saved.text
    seen = []
    mock_provider(
        monkeypatch,
        lambda request: (
            seen.append(request.headers["Authorization"]) or httpx.Response(200, json={"data": []})
        ),
    )
    assert (
        client.post(
            "/api/v1/model-settings/models?purpose=embedding",
            json={"base_url": "https://provider.example.test/v1"},
        ).status_code
        == 200
    )
    assert seen == ["Bearer offline-vector-secret-key"]
    assert client.get("/workspace-config").json()["vision_recognition_available"] is True
    assert client.get("/workspace-config").json()["embedding_enabled"] is True
    assert client.delete("/api/v1/model-settings?purpose=vision").status_code == 200
    assert store.read("vision") is None
    assert store.require().api_key.get_secret_value() == "offline-chat"
    assert store.require("embedding").api_key.get_secret_value() == "offline-vector-secret-key"
    assert client.get("/workspace-config").json()["vision_recognition_available"] is False


def test_legacy_chat_configuration_survives_first_auxiliary_save(client_store):
    _, store = client_store
    store.save(profile(key="offline-legacy"))
    raw = json.loads(store.path.read_text(encoding="utf-8"))["profiles"]["chat"]
    raw.pop("enabled")
    raw.pop("dimensions")
    store.path.write_text(json.dumps({"version": 1, **raw}), encoding="utf-8")
    assert store.require().api_key.get_secret_value() == "offline-legacy"
    store.save(profile("image", key="offline-image"), "vision")
    assert store.require().api_key.get_secret_value() == "offline-legacy"
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2


def test_embedding_probe_discovers_actual_dimensions_without_saving(client_store, monkeypatch):
    client, store = client_store

    def handler(request):
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content)["input"] == ["Connection check."]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.2, 0.3, 0.4]}]})

    mock_provider(monkeypatch, handler)
    body = {
        "base_url": "https://provider.example.test/v1",
        "model": "offline-vector",
        "api_key": "offline-vector-key",
    }
    result = client.post("/api/v1/model-settings/check?purpose=embedding", json=body)
    assert result.status_code == 200 and result.json()["dimensions"] == 3
    assert store.read("embedding") is None
    assert client.put("/api/v1/model-settings?purpose=embedding", json=body).status_code == 422
    assert (
        client.post(
            "/api/v1/model-settings/check?purpose=embedding", json={**body, "dimensions": 4}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/model-settings?purpose=embedding", json={**body, "dimensions": 3}
        ).status_code
        == 200
    )


def test_deepseek_website_is_not_accepted_as_the_api_endpoint(client_store):
    client, _ = client_store
    result = client.post(
        "/api/v1/model-settings/models",
        json={"base_url": "https://www.deepseek.com/v1", "api_key": "offline-secret"},
    )
    assert result.status_code == 422 and "api.deepseek.com" in result.text
    assert "offline-secret" not in result.text


def test_vision_probe_uses_its_own_key_and_a_synthetic_image_with_custom_provider(
    client_store, monkeypatch
):
    import struct

    from app.vision.provider import VisionProvider

    # The probe must be a valid synthetic PNG, not an uploaded business image.
    assert provider_api.PROBE_PNG.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", provider_api.PROBE_PNG[16:24]) == (128, 64)
    client, store = client_store
    store.save(profile(key="offline-owner-chat-key"))
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "custom-vision.example.test"
        assert request.headers["authorization"] == "Bearer offline-user-image-key"
        body = json.loads(request.content)
        assert body["model"] == "offline-image-model"
        assert body["messages"][0]["content"][0]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"text": "TEST", "tables": [], "warnings": []})
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.vision.provider.VisionProvider",
        lambda settings: VisionProvider(settings, transport=httpx.MockTransport(handler)),
    )
    result = client.post(
        "/api/v1/model-settings/check?purpose=vision",
        json={
            "base_url": "https://custom-vision.example.test/v1",
            "model": "offline-image-model",
            "api_key": "offline-user-image-key",
        },
    )
    assert result.status_code == 200 and result.json()["vision_supported"] is True
    assert len(requests) == 1 and store.read("vision") is None
    assert store.require().api_key.get_secret_value() == "offline-owner-chat-key"


def test_unconfigured_auxiliary_services_never_use_environment_keys(client_store):
    _, store = client_store
    runtime = LocalRuntimeModels(
        store,
        Settings(
            _env_file=None,
            embedding_enabled=True,
            embedding_api_key="offline-owner-key",
            embedding_api_base="https://owner.test/v1",
            embedding_model="owner",
            embedding_dimensions=3,
            vision_enabled=True,
            vision_api_key="offline-owner-key",
            vision_model="owner",
        ),
    )
    assert not runtime.available("embedding") and not runtime.available("vision")

    async def run():
        async with runtime.embedding() as embedder:
            assert embedder is None
        async with runtime.vision() as provider:
            assert provider is None

    asyncio.run(run())


def test_auxiliary_runtime_snapshots_pin_key_model_and_embedding_revision(tmp_path, monkeypatch):
    store = LocalModelStore(tmp_path / "model.json")
    closed = []

    class FakeEmbedder:
        def __init__(self, settings):
            self.profile = EmbeddingProfile.create(
                settings.embedding_api_base,
                settings.embedding_model,
                settings.embedding_dimensions,
                settings.embedding_revision,
            )
            self.key = settings.embedding_api_key

        async def aclose(self):
            closed.append(self.key)

    class FakeVision:
        def __init__(self, settings):
            self.model, self.key = settings.vision_model, settings.vision_api_key

        async def aclose(self):
            closed.append(self.key)

    monkeypatch.setattr("app.knowledge.embedding.APIEmbedder", FakeEmbedder)
    monkeypatch.setattr("app.vision.provider.VisionProvider", FakeVision)
    store.save(profile("vector-one", key="offline-vector-one", dimensions=3), "embedding")
    store.save(profile("vision-one", key="offline-vision-one"), "vision")
    runtime = LocalRuntimeModels(store, Settings(_env_file=None))
    knowledge = KnowledgeService(None, Settings(_env_file=None), local_models=runtime)
    vision = VisionService(None, Settings(_env_file=None), local_models=runtime)

    async def run():
        async with knowledge.embedding_session():
            first = knowledge.embedder.profile
            store.save(profile("vector-two", key="offline-vector-two", dimensions=4), "embedding")
            assert (
                knowledge.embedder.profile == first
                and knowledge.embedder.key == "offline-vector-one"
            )
            async with knowledge.embedding_session():
                assert knowledge.embedder.profile == first
        async with knowledge.embedding_session():
            assert (
                knowledge.embedder.profile != first and knowledge.embedder.profile.dimensions == 4
            )
        async with vision.vision_session():
            store.save(profile("vision-two", key="offline-vision-two"), "vision")
            assert (
                vision.provider.model == "vision-one"
                and vision.provider.key == "offline-vision-one"
            )
        async with vision.vision_session():
            assert vision.provider.model == "vision-two"

    asyncio.run(run())
    assert sorted(closed) == sorted(
        ["offline-vector-one", "offline-vector-two", "offline-vision-one", "offline-vision-two"]
    )


def test_worker_pins_one_profile_through_claim_and_processing(tmp_path, monkeypatch):
    store = LocalModelStore(tmp_path / "model.json")

    class FakeEmbedder:
        def __init__(self, settings):
            self.profile = EmbeddingProfile.create(
                settings.embedding_api_base,
                settings.embedding_model,
                settings.embedding_dimensions,
                settings.embedding_revision,
            )

        async def aclose(self):
            pass

    monkeypatch.setattr("app.knowledge.embedding.APIEmbedder", FakeEmbedder)
    store.save(profile("first", dimensions=3), "embedding")
    settings = Settings(_env_file=None)
    knowledge = KnowledgeService(None, settings, local_models=LocalRuntimeModels(store, settings))
    worker = IndexWorker(knowledge)

    async def claim():
        assert knowledge.embedder.profile.model == "first"
        store.save(profile("second", dimensions=4), "embedding")
        return ("job", "document", "lease", 1)

    async def process(claim):
        assert knowledge.embedder.profile.model == "first"

    async def heartbeat(claim):
        await asyncio.Future()

    worker.claim, worker.process, worker.heartbeat = claim, process, heartbeat
    assert asyncio.run(worker.run_once()) is True
    assert store.require("embedding").model == "second"
