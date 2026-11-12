"""Real OCR review/vector-index pipelines with personal settings and mocked transports."""

import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from app.infrastructure.database.models import VisionDraft
from app.infrastructure.database.pg_models import ChunkEmbedding
from app.infrastructure.llm.local_runtime import LocalRuntimeModels
from app.infrastructure.llm.local_settings import LocalModelStore
from app.knowledge.embedding import APIEmbedder
from app.knowledge.worker import IndexWorker
from app.vision.provider import VisionProvider
from app.vision.service import VisionService
from app.vision.types import VisionError
from tests.conftest import run
from tests.test_phase3_postgres import env  # noqa: F401

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


def configure(env, tmp_path):
    store = LocalModelStore(tmp_path / "personal-models.json")
    runtime = LocalRuntimeModels(store, env.settings)
    env.client.app.state.local_model_store = store
    env.client.app.state.local_runtime_models = runtime
    env.service.local_models = runtime
    return store, runtime


def test_personal_embedding_upload_index_search_and_model_switch(env, tmp_path, monkeypatch):
    _, _ = configure(env, tmp_path)
    marker = uuid.uuid4().hex
    calls = []

    def transport(request):
        payload = json.loads(request.content)
        calls.append((payload["model"], request.headers["Authorization"]))
        assert request.url.host == "personal-vector.example.test"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, 0.1, 0.1, 0.1]}
                    for i, _ in enumerate(payload["input"])
                ]
            },
        )

    monkeypatch.setattr(
        "app.knowledge.embedding.APIEmbedder",
        lambda settings: APIEmbedder(settings, transport=httpx.MockTransport(transport)),
    )

    def save(name, key):
        response = env.client.put(
            "/api/v1/model-settings?purpose=embedding",
            json={
                "base_url": "https://personal-vector.example.test/v1",
                "model": name,
                "api_key": key,
                "dimensions": 4,
                "enabled": True,
            },
        )
        assert response.status_code == 200

    first, second = "offline-first-" + marker, "offline-second-" + marker
    save(first, "offline-first-user-key")

    async def scenario():
        identifier = await env.add(["合成个人向量接入测试：收入规则。"])
        await env.enqueue(identifier)
        worker = IndexWorker(env.service, document_ids=env.owned)
        assert await worker.run_once()
        state = await env.service.index_state(identifier)
        assert state["index_status"] == "ready" and not state["requires_reindex"]
        assert state["process_embedding_usage"]["requests"] == 1
        assert (await env.service.search("收入", mode="vector", document_ids=[identifier])).hits
        count = len(calls)
        save(second, "offline-second-user-key")
        assert len(calls) == count  # Saving never embeds or starts a reindex.
        assert (await env.service.details(identifier))["requires_reindex"] is True
        assert not (await env.service.search("收入", mode="vector", document_ids=[identifier])).hits
        assert len(calls) == count  # Old vectors are not compared against the new model.
        await env.enqueue(identifier)
        assert await worker.run_once()
        assert not (await env.service.index_state(identifier))["requires_reindex"]
        assert (await env.service.search("收入", mode="vector", document_ids=[identifier])).hits
        async with env.factory() as session:
            from app.infrastructure.database.models import DocumentChunk

            models = set(
                await session.scalars(
                    select(ChunkEmbedding.model)
                    .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
                    .where(DocumentChunk.document_id == identifier)
                )
            )
        assert models == {first, second}

    run(scenario())
    assert calls and all(
        key
        == ("Bearer offline-first-user-key" if name == first else "Bearer offline-second-user-key")
        for name, key in calls
    )


def test_personal_vision_recognition_uses_saved_key_only_after_consent(env, tmp_path, monkeypatch):
    _, runtime = configure(env, tmp_path)
    calls = []

    def transport(request):
        calls.append(request)
        assert request.url.host == "personal-image.example.test"
        assert request.headers["Authorization"] == "Bearer offline-image-user-key"
        assert json.loads(request.content)["model"] == "offline-personal-vision"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"text": "离线合成 OCR 001", "tables": [], "warnings": []},
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "app.vision.provider.VisionProvider",
        lambda settings: VisionProvider(settings, transport=httpx.MockTransport(transport)),
    )
    assert (
        env.client.put(
            "/api/v1/model-settings?purpose=vision",
            json={
                "base_url": "https://personal-image.example.test/v1",
                "model": "offline-personal-vision",
                "api_key": "offline-image-user-key",
            },
        ).status_code
        == 200
    )
    assert calls == []
    settings = env.settings.model_copy(update={"vision_dir": tmp_path / "vision"})
    service = VisionService(env.factory, settings, local_models=runtime)

    async def scenario():
        raw = (Path(__file__).parent / "fixtures" / "vision_synthetic.png").read_bytes()
        draft = await service.stage(raw, "synthetic-personal-model.png", "image/png")
        try:
            with pytest.raises(VisionError, match="同意"):
                await service.recognize(draft["id"], draft["revision"], [1], False)
            assert calls == []
            result = await service.recognize(draft["id"], draft["revision"], [1], True)
            assert result["status"] == "needs_review"
            assert result["pages"]["1"]["model"] == "offline-personal-vision"
            assert result["pages"]["1"]["raw"]["text"] == "离线合成 OCR 001"
        finally:
            async with env.factory() as session, session.begin():
                await session.execute(delete(VisionDraft).where(VisionDraft.id == draft["id"]))

    run(scenario())
    assert len(calls) == 1
