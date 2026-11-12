"""At-least-once index worker with SKIP LOCKED claims, leases and fenced writes."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import timedelta

from loguru import logger
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database.models import Document, DocumentChunk, IndexJob, _utcnow
from app.knowledge.embedding import validate_vectors
from app.knowledge.types import EmbeddingError


class LeaseLost(Exception):
    pass


class IndexWorker:
    def __init__(self, service, *, document_ids=None):
        self.service, self.factory, self.settings = service, service.factory, service.settings
        self.document_ids = document_ids
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._loop(), name="document-index-worker")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.CancelledError, TimeoutError):
                pass
            except Exception as exc:
                # A database outage must not prevent closing the API clients/engine.
                logger.warning("索引工作者关闭时数据库不可用: {}", type(exc).__name__)
            finally:
                self._task = None

    async def _loop(self):
        while True:
            try:
                await self.service.cleanup_deletions()
                if self.service.embedding_enabled and await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("文档后台任务暂不可用: {}", type(exc).__name__)
            await asyncio.sleep(self.settings.index_poll_seconds)

    async def claim(self):
        profile = self.service.embedder.profile
        now = _utcnow()
        async with self.factory() as session, session.begin():
            statement = (
                select(IndexJob)
                .where(
                    IndexJob.model == profile.model,
                    IndexJob.revision == profile.revision,
                    IndexJob.dimensions == profile.dimensions,
                    or_(
                        and_(IndexJob.status == "pending", IndexJob.available_at <= now),
                        and_(IndexJob.status == "processing", IndexJob.locked_until <= now),
                    ),
                )
                .order_by(IndexJob.available_at, IndexJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if self.document_ids is not None:
                statement = statement.where(IndexJob.document_id.in_(self.document_ids))
            job = await session.scalar(statement)
            if job is None:
                return None
            job.status, job.lease_token = "processing", str(uuid.uuid4())
            job.locked_until = now + timedelta(seconds=self.settings.index_lease_seconds)
            job.attempts += 1
            return job.id, job.document_id, job.lease_token, job.attempts

    async def _lock(self, session, claim):
        job_id, document_id, token, _ = claim
        # Consistent lock order with reindex and DELETE: document, then job.
        doc = await session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        job = await session.scalar(select(IndexJob).where(IndexJob.id == job_id).with_for_update())
        if (
            doc is None
            or doc.status != "ready"
            or job is None
            or job.lease_token != token
            or job.status != "processing"
        ):
            raise LeaseLost()
        return doc, job

    async def heartbeat(self, claim):
        while True:
            await asyncio.sleep(self.settings.index_lease_seconds / 3)
            async with self.factory() as session, session.begin():
                result = await session.execute(
                    update(IndexJob)
                    .where(
                        IndexJob.id == claim[0],
                        IndexJob.lease_token == claim[2],
                        IndexJob.status == "processing",
                    )
                    .values(
                        locked_until=_utcnow()
                        + timedelta(seconds=self.settings.index_lease_seconds)
                    )
                )
                if result.rowcount != 1:
                    return

    async def run_once(self):
        async with self.service.embedding_session():
            return await self._run_once()

    async def _run_once(self):
        if self.service.embedder is None:
            return False
        claim = await self.claim()
        if claim is None:
            return False
        heartbeat = asyncio.create_task(self.heartbeat(claim))
        try:
            if claim[3] > self.settings.index_max_attempts:
                raise EmbeddingError("index_attempts_exhausted")
            await self.process(claim)
        except asyncio.CancelledError:
            with suppress(LeaseLost):
                await asyncio.shield(
                    self.fail(claim, "index_interrupted", retryable=True, interrupted=True)
                )
            raise
        except LeaseLost:
            pass
        except Exception as exc:
            code = str(exc) if isinstance(exc, EmbeddingError) else "index_storage_error"
            logger.warning("索引任务失败 {}: {}", code, type(exc).__name__)
            retryable = exc.retryable if isinstance(exc, EmbeddingError) else True
            with suppress(LeaseLost):
                await self.fail(claim, code, retryable=retryable)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        return True

    async def fail(self, claim, error, *, retryable: bool, interrupted: bool = False):
        async with self.factory() as session, session.begin():
            doc, job = await self._lock(session, claim)
            pending = retryable and (interrupted or job.attempts < self.settings.index_max_attempts)
            job.status = "pending" if pending else "failed"
            job.error, doc.index_error = error, error
            job.lease_token, job.locked_until = None, None
            job.available_at = _utcnow() + timedelta(
                seconds=0 if interrupted else self.settings.index_retry_seconds
            )
            doc.index_status = job.status

    async def process(self, claim):
        from app.infrastructure.database.pg_models import ChunkEmbedding

        profile = self.service.embedder.profile
        async with self.factory() as session, session.begin():
            doc, job = await self._lock(session, claim)
            doc.index_status, doc.index_error = "processing", None
            chunks = list(
                (
                    await session.execute(
                        select(DocumentChunk.id, DocumentChunk.content)
                        .where(DocumentChunk.document_id == doc.id)
                        .order_by(DocumentChunk.chunk_index, DocumentChunk.id)
                    )
                ).all()
            )
            existing = set(
                await session.scalars(
                    select(ChunkEmbedding.chunk_id).where(
                        ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
                        ChunkEmbedding.model == profile.model,
                        ChunkEmbedding.revision == profile.revision,
                        ChunkEmbedding.dimensions == profile.dimensions,
                    )
                )
            )
            job.total_chunks, job.completed_chunks = len(chunks), len(existing)
        if not chunks:
            raise EmbeddingError("document_has_no_chunks")
        missing = [chunk for chunk in chunks if chunk.id not in existing]
        size = self.settings.embedding_batch_size
        for start in range(0, len(missing), size):
            batch = missing[start : start + size]
            vectors = validate_vectors(
                await self.service.embedder.embed([c.content for c in batch]),
                len(batch),
                profile.dimensions,
            )
            async with self.factory() as session, session.begin():
                _, job = await self._lock(session, claim)
                values = [
                    {
                        "id": str(uuid.uuid4()),
                        "chunk_id": chunk.id,
                        "model": profile.model,
                        "revision": profile.revision,
                        "dimensions": profile.dimensions,
                        "embedding": vector,
                        "created_at": _utcnow(),
                    }
                    for chunk, vector in zip(batch, vectors)
                ]
                statement = insert(ChunkEmbedding).values(values)
                await session.execute(
                    statement.on_conflict_do_update(
                        constraint="uq_chunk_embedding_version",
                        set_={
                            "embedding": statement.excluded.embedding,
                            "dimensions": statement.excluded.dimensions,
                        },
                    )
                )
                job.completed_chunks += len(batch)
        async with self.factory() as session, session.begin():
            doc, job = await self._lock(session, claim)
            job.status, job.error, job.lease_token, job.locked_until = "succeeded", None, None, None
            doc.index_status, doc.index_error = "ready", None
            doc.index_model, doc.index_revision, doc.index_dimensions = (
                profile.model,
                profile.revision,
                profile.dimensions,
            )
