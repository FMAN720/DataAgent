"""Real pgvector pipeline with deterministic synthetic embeddings, no external API."""

import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import Document, DocumentChunk, IndexJob, _utcnow
from app.infrastructure.database.session import init_engine
from app.knowledge.service import KnowledgeService
from app.knowledge.types import EmbeddingError, EmbeddingProfile, KnowledgeError
from app.knowledge.worker import IndexWorker, LeaseLost
from app.main import create_app
from tests.conftest import run

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


class SyntheticEmbedder:
    profile = EmbeddingProfile.create("synthetic://offline", "synthetic-test-only", 4, "v1")

    def __init__(self):
        self.calls = []
        self.fail_at = None

    async def embed(self, texts):
        self.calls.append(texts)
        if self.fail_at == len(self.calls):
            self.fail_at = None
            raise EmbeddingError("synthetic_transient", retryable=True)

        def encode(text):
            if any(word in text for word in ["净销售额", "收入", "营收", "实收"]):
                return [1.0, 0.0, 0.0, 0.0]
            if any(word in text for word in ["退款率", "退货比例"]):
                return [0.0, 1.0, 0.0, 0.0]
            if any(word in text for word in ["配送", "多久送达"]):
                return [0.0, 0.0, 1.0, 0.0]
            return [0.0, 0.0, 0.0, 1.0]

        return [encode(text) for text in texts]


@pytest.fixture
def env(tmp_path):
    values = load_config()
    settings = Settings(
        _env_file=None,
        database_url=values["DATABASE_URL"],
        upload_dir=tmp_path / "uploads",
        embedding_batch_size=2,
        index_retry_seconds=0,
        index_poll_seconds=0.02,
    )
    engine = init_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    embedder = SyntheticEmbedder()
    service = KnowledgeService(factory, settings, embedder)
    owned = set()

    async def add(texts, filename="synthetic.md", storage_path=None):
        async with factory() as session, session.begin():
            doc = Document(
                filename=filename,
                status="ready",
                index_status="not_indexed",
                storage_path=storage_path,
                meta={"chunk_count": len(texts)},
            )
            session.add(doc)
            await session.flush()
            owned.add(doc.id)
            for i, content in enumerate(texts):
                session.add(DocumentChunk(document_id=doc.id, chunk_index=i, content=content))
            return doc.id

    async def enqueue(document_id, force=False):
        async with factory() as session, session.begin():
            job = await service.enqueue(session, document_id, force=force)
            return job.id

    app = create_app()
    app.state.document_session_factory = factory
    app.state.knowledge_service = service
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    def upload(content):
        response = client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.md", content.encode(), "text/markdown")},
        )
        if response.status_code == 200 and not response.json()["duplicate"]:
            owned.add(response.json()["document_id"])
        return response

    state = SimpleNamespace(
        service=service,
        factory=factory,
        embedder=embedder,
        settings=settings,
        worker=IndexWorker(service),
        add=add,
        enqueue=enqueue,
        client=client,
        owned=owned,
        upload=upload,
    )
    yield state
    client.close()

    async def cleanup():
        async with factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.id.in_(owned)))
        await engine.dispose()

    run(cleanup())


def test_real_vector_roundtrip_hybrid_scoping_and_provider_isolation(env):
    async def scenario():
        sales = await env.add(["净销售额排除已退款订单。"])
        refunds = await env.add(["退款率等于退款订单数除以支付订单数。"])
        for document_id in (sales, refunds):
            await env.enqueue(document_id)
            assert await env.worker.run_once()
        state = await env.service.index_state(sales)
        assert state["index_status"] == "ready" and state["job"]["completed_chunks"] == 1
        result = await env.service.search("实收收入", mode="vector", document_ids=[sales, refunds])
        assert result.hits and result.hits[0].document_id == sales
        hybrid = await env.service.search("净销售额", document_ids=[sales, refunds])
        assert hybrid.mode == "hybrid" and hybrid.hits[0].document_id == sales
        assert not (
            await env.service.search("净销售额", mode="keyword", document_ids=[refunds])
        ).hits
        env.embedder.profile = EmbeddingProfile.create(
            "synthetic://another-provider", "synthetic-test-only", 4, "v1"
        )
        previous_calls = len(env.embedder.calls)
        assert not (await env.service.search("净销售额", mode="vector", document_ids=[sales])).hits
        assert len(env.embedder.calls) == previous_calls

    run(scenario())


def test_partial_index_retries_only_missing_batches(env):
    async def scenario():
        document_id = await env.add([f"净销售额第 {i} 块" for i in range(5)])
        await env.enqueue(document_id)
        env.embedder.fail_at = 2
        assert await env.worker.run_once()
        state = await env.service.index_state(document_id)
        assert state["index_status"] == "pending" and state["job"]["completed_chunks"] == 2
        assert not (
            await env.service.search("净销售额", mode="vector", document_ids=[document_id])
        ).hits
        # New worker instance represents process recovery; it reuses committed chunks.
        assert await IndexWorker(env.service).run_once()
        state = await env.service.index_state(document_id)
        assert state["index_status"] == "ready" and state["job"]["attempts"] == 2
        flattened = [text for batch in env.embedder.calls for text in batch]
        assert flattened.count("净销售额第 0 块") == 1
        assert flattened.count("净销售额第 1 块") == 1

    run(scenario())


def test_expired_lease_is_reclaimed_and_old_worker_is_fenced(env):
    async def scenario():
        doc = await env.add(["净销售额租约测试"])
        await env.enqueue(doc)
        old = await env.worker.claim()
        async with env.factory() as session, session.begin():
            job = await session.get(IndexJob, old[0])
            job.locked_until = _utcnow() - timedelta(seconds=1)
        newer = await IndexWorker(env.service).claim()
        assert old[2] != newer[2]
        with pytest.raises(LeaseLost):
            await env.worker.process(old)
        await env.worker.process(newer)
        assert (await env.service.index_state(doc))["index_status"] == "ready"

    run(scenario())


def test_concurrent_claims_do_not_take_the_same_job(env):
    async def scenario():
        doc = await env.add(["净销售额并发领取"])
        await env.enqueue(doc)
        claims = await asyncio.gather(env.worker.claim(), IndexWorker(env.service).claim())
        assert sum(claim is not None for claim in claims) == 1

    run(scenario())


def test_force_reindex_invalidates_inflight_job(env):
    async def scenario():
        doc = await env.add(["净销售额重建"])
        await env.enqueue(doc)
        old = await env.worker.claim()
        await env.enqueue(doc, force=True)
        with pytest.raises(LeaseLost):
            await env.worker.process(old)
        await env.worker.run_once()
        calls = len(env.embedder.calls)
        await env.enqueue(doc)
        assert not await env.worker.run_once() and len(env.embedder.calls) == calls
        await env.enqueue(doc, force=True)
        await env.worker.run_once()
        assert len(env.embedder.calls) == calls + 1

    run(scenario())


def test_deletion_cascades_vectors_jobs_and_blocks_worker_resurrection(env):
    async def scenario():
        doc = await env.add(["净销售额删除测试"])
        await env.enqueue(doc)
        old = await env.worker.claim()
        assert await env.service.delete_document(doc)
        with pytest.raises(LeaseLost):
            await env.worker.process(old)
        async with env.factory() as session:
            assert await session.get(Document, doc) is None
            assert not await session.scalar(select(IndexJob.id).where(IndexJob.document_id == doc))
        assert not (await env.service.search("净销售额", document_ids=[doc])).hits

    run(scenario())


def test_api_upload_dedup_search_status_retry_and_delete(env):
    env.service.embedder = None
    content = f"净销售额排除已退款和已取消的订单。测试标记 {uuid.uuid4()}"
    first = env.upload(content)
    assert first.status_code == 200
    document_id = first.json()["document_id"]
    duplicate = env.upload(content)
    assert duplicate.json()["duplicate"] and duplicate.json()["document_id"] == document_id
    assert len(list(env.settings.upload_dir.iterdir())) == 1
    state = env.client.get(f"/api/v1/documents/{document_id}/index").json()
    assert state["index_status"] == "not_indexed" and state["job"] is None
    response = env.client.post(
        "/api/v1/documents/search", json={"query": "净销售额", "document_ids": [document_id]}
    )
    assert response.status_code == 200 and response.json()["mode"] == "keyword"
    assert response.json()["hits"][0]["document_id"] == document_id
    assert env.client.post(f"/api/v1/documents/{document_id}/index").status_code == 503
    env.service.embedder = env.embedder
    assert env.client.post(f"/api/v1/documents/{document_id}/index").status_code == 202
    run(env.worker.run_once())
    assert (
        env.client.get(f"/api/v1/documents/{document_id}/index").json()["index_status"] == "ready"
    )
    assert env.client.delete(f"/api/v1/documents/{document_id}").json()["status"] == "deleted"
    assert not list(env.settings.upload_dir.iterdir())
    assert env.client.get(f"/api/v1/documents/{document_id}/index").status_code == 404


def test_concurrent_duplicate_uploads_leave_one_document_and_file(env):
    import httpx

    env.service.embedder = None
    original = env.service.duplicate

    async def scenario():
        count = 0
        ready = asyncio.Event()

        async def barrier(session, content_hash):
            nonlocal count
            result = await original(session, content_hash)
            count += 1
            if count == 2:
                ready.set()
            if count <= 2:
                await asyncio.wait_for(ready.wait(), 5)
            return result

        env.service.duplicate = barrier
        content = f"并发去重测试 {uuid.uuid4()}".encode()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.client.app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/api/v1/documents/upload",
                        files={"file": ("duplicate.txt", content, "text/plain")},
                    )
                    for _ in range(2)
                )
            )
        for response in responses:
            if response.status_code == 200:
                env.owned.add(response.json()["document_id"])
        assert all(response.status_code == 200 for response in responses)
        assert len({response.json()["document_id"] for response in responses}) == 1
        assert sum(response.json()["duplicate"] for response in responses) == 1
        assert len(list(env.settings.upload_dir.iterdir())) == 1

    run(scenario())


def test_file_cleanup_error_is_durable_and_retried(env, monkeypatch):
    response = env.upload(f"删除恢复测试 {uuid.uuid4()}")
    doc = response.json()["document_id"]
    saved = next(env.settings.upload_dir.iterdir())
    original = Path.unlink

    def fail(path, **kwargs):
        if path == saved:
            raise PermissionError("synthetic file lock")
        return original(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert not run(env.service.delete_document(doc))
        assert run(env.service.index_state(doc))["upload_status"] == "deleting"
        assert not run(env.service.search("删除恢复", document_ids=[doc])).hits
    run(env.service.cleanup_deletions())
    assert not saved.exists()
    with pytest.raises(KnowledgeError):
        run(env.service.index_state(doc))


def test_invalid_vectors_fail_job_and_remain_unsearchable(env):
    async def scenario():
        doc = await env.add(["净销售额错误维度"])
        await env.enqueue(doc)

        async def bad(texts):
            return [[1, 0] for _ in texts]

        env.embedder.embed = bad
        await env.worker.run_once()
        state = await env.service.index_state(doc)
        assert state["index_status"] == "failed" and "dimensions" in state["error"]

    run(scenario())


def test_manual_retry_preserves_previously_completed_batches(env):
    async def scenario():
        doc = await env.add([f"净销售额手动重试 {i}" for i in range(3)])
        env.settings.index_max_attempts = 1
        await env.enqueue(doc)
        env.embedder.fail_at = 2
        await env.worker.run_once()
        assert (await env.service.index_state(doc))["index_status"] == "failed"
        async with env.factory() as session, session.begin():
            await env.service.enqueue(session, doc, retry=True)
        await env.worker.run_once()
        assert (await env.service.index_state(doc))["index_status"] == "ready"
        assert sum(batch.count("净销售额手动重试 0") for batch in env.embedder.calls) == 1

    run(scenario())


def test_outside_file_path_is_never_deleted(env, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("do not remove", encoding="utf-8")

    async def scenario():
        doc = await env.add(["删除边界"], storage_path=str(outside))
        assert not await env.service.delete_document(doc)
        assert outside.exists()
        assert not (await env.service.search("删除边界", document_ids=[doc])).hits

    run(scenario())


def test_bm25_fallback_no_answer_dedup_and_context_budget(env):
    async def scenario():
        doc = await env.add(["净销售额：" + "业务规则。" * 500] * 2)
        env.service.embedder = None
        env.settings.rag_context_max_bytes = 300
        response = await env.service.search("净销售额", document_ids=[doc])
        assert response.mode == "keyword" and response.warnings
        assert len(response.hits) == 1
        assert sum(len(hit.content.encode()) for hit in response.hits) <= 300
        assert not (await env.service.search("火星轨道参数", document_ids=[doc])).hits
        assert not (await env.service.search("净销售额", document_ids=[])).hits
        with pytest.raises(KnowledgeError):
            await env.service.search("x", document_ids=["invalid"])

    run(scenario())


def test_embedding_failure_reports_keyword_fallback(env):
    async def scenario():
        doc = await env.add(["净销售额降级"])
        await env.enqueue(doc)
        await env.worker.run_once()
        env.embedder.fail_at = len(env.embedder.calls) + 1
        result = await env.service.search("净销售额", document_ids=[doc])
        assert result.mode == "keyword" and result.hits
        assert "synthetic_transient" in result.warnings[0]

    run(scenario())


def test_cancellation_requeues_and_worker_recovers(env):
    async def scenario():
        doc = await env.add(["净销售额取消任务"])
        await env.enqueue(doc)
        original = env.embedder.embed
        started = asyncio.Event()

        async def blocked(texts):
            started.set()
            await asyncio.Event().wait()

        env.embedder.embed = blocked
        task = asyncio.create_task(env.worker.run_once())
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await env.service.index_state(doc))["index_status"] == "pending"
        env.embedder.embed = original
        await env.worker.run_once()
        assert (await env.service.index_state(doc))["index_status"] == "ready"

    run(scenario())


def test_scoped_worker_never_claims_another_documents_pending_job(env):
    async def scenario():
        foreign = await env.add(["不在评测授权范围内的合成文本"])
        approved = await env.add(["仅此合成文本可用于联调"])
        await env.enqueue(foreign)
        await env.enqueue(approved)
        scoped = IndexWorker(env.service, document_ids=[approved])
        assert await scoped.run_once()
        assert not await scoped.run_once()
        assert (await env.service.index_state(foreign))["index_status"] == "pending"
        assert (await env.service.index_state(approved))["index_status"] == "ready"
        assert env.embedder.calls == [["仅此合成文本可用于联调"]]

    run(scenario())
