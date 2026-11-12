"""PostgreSQL document storage, scoped keyword/vector retrieval and durable jobs."""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date
from functools import wraps
from pathlib import Path
from uuid import UUID

from sqlalchemy import Float, cast, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.rag.retriever import BM25Index
from app.infrastructure.database.models import Document, DocumentChunk, IndexJob, _utcnow
from app.knowledge.types import (
    DOCUMENT_TYPES,
    AsyncEmbedder,
    EmbeddingError,
    KnowledgeError,
    SearchHit,
    SearchResponse,
)


def document_conditions(*, date_from=None, date_to=None, document_type=None):
    """Filter by explicit business document date; undated rows never satisfy a range."""

    def parse(value):
        if value is None:
            return None
        if type(value) is date:
            return value
        if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise KnowledgeError("文档日期必须为 YYYY-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise KnowledgeError("文档日期必须为 YYYY-MM-DD") from None

    start, end = parse(date_from), parse(date_to)
    if start and end and start > end:
        raise KnowledgeError("开始日期不能晚于结束日期")
    if document_type is not None and (
        not isinstance(document_type, str) or document_type not in DOCUMENT_TYPES
    ):
        raise KnowledgeError("不支持的文档类型")
    conditions = [Document.status == "ready"]
    if start:
        conditions.append(Document.document_date >= start)
    if end:
        conditions.append(Document.document_date <= end)
    if document_type:
        conditions.append(Document.document_type == document_type)
    return conditions


def trim_utf8(value: str, max_bytes: int) -> str:
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def rank_corpus(rows, query: str, top_k: int):
    index = BM25Index()
    index.add_documents({row.id: row.content for row in rows})
    return index.search(query, top_k)


def fuse_ranks(lists: list[list[tuple[str, float]]], top_k: int):
    scores: dict[str, float] = {}
    for items in lists:
        for rank, (chunk_id, _) in enumerate(items):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]


def embedding_operation(method):
    @wraps(method)
    async def scoped(self, *args, **kwargs):
        async with self.embedding_session():
            return await method(self, *args, **kwargs)

    return scoped


class KnowledgeService:
    def __init__(
        self,
        factory: async_sessionmaker,
        settings: Settings,
        embedder: AsyncEmbedder | None = None,
        *,
        local_models=None,
    ):
        self.factory, self.settings, self._embedder = factory, settings, embedder
        self.local_models = local_models
        self._embedding_context: ContextVar[tuple[bool, AsyncEmbedder | None]] = ContextVar(
            "embedding_snapshot", default=(False, None)
        )

    @property
    def embedder(self):
        active, embedder = self._embedding_context.get()
        return embedder if active else self._embedder

    @embedder.setter
    def embedder(self, value):
        self._embedder = value

    @property
    def embedding_enabled(self):
        active, embedder = self._embedding_context.get()
        if active:
            return embedder is not None
        return (
            self.local_models.available("embedding")
            if self.local_models
            else self._embedder is not None
        )

    @asynccontextmanager
    async def embedding_session(self):
        if self._embedding_context.get()[0] or self.local_models is None:
            yield
            return
        async with self.local_models.embedding() as embedder:
            token = self._embedding_context.set((True, embedder))
            try:
                yield
            finally:
                self._embedding_context.reset(token)

    async def check_schema(self):
        # Import pgvector only on an explicitly configured document database path.
        from app.infrastructure.database.pg_models import ChunkEmbedding

        async with self.factory() as session:
            await session.execute(select(Document).limit(0))
            await session.execute(select(IndexJob).limit(0))
            await session.execute(select(ChunkEmbedding.id).limit(0))

    async def duplicate(self, session: AsyncSession, content_hash: str):
        doc = await session.scalar(select(Document).where(Document.content_hash == content_hash))
        if doc and doc.status != "ready":
            raise KnowledgeError("文档正在清理，请稍后重试")
        return doc

    @embedding_operation
    async def enqueue(
        self, session: AsyncSession, document_id: str, *, force: bool = False, retry: bool = False
    ):
        if self.embedder is None:
            raise EmbeddingError("Embedding API 未启用或未配置")
        doc = await session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if doc is None or doc.status != "ready":
            raise KnowledgeError("文档不存在或正在删除")
        job = await session.scalar(
            select(IndexJob).where(IndexJob.document_id == document_id).with_for_update()
        )
        profile = self.embedder.profile
        same = job and (job.model, job.revision, job.dimensions) == (
            profile.model,
            profile.revision,
            profile.dimensions,
        )
        if (
            job is not None
            and same
            and not force
            and (
                job.status in ("pending", "processing", "succeeded")
                or (job.status == "failed" and not retry)
            )
        ):
            return job
        if force:
            from app.infrastructure.database.pg_models import ChunkEmbedding

            chunks = select(DocumentChunk.id).where(DocumentChunk.document_id == document_id)
            await session.execute(
                delete(ChunkEmbedding).where(
                    ChunkEmbedding.chunk_id.in_(chunks),
                    ChunkEmbedding.model == profile.model,
                    ChunkEmbedding.revision == profile.revision,
                )
            )
        if job is None:
            job = IndexJob(document_id=document_id)
            session.add(job)
        job.model, job.revision, job.dimensions = (
            profile.model,
            profile.revision,
            profile.dimensions,
        )
        job.status, job.attempts, job.completed_chunks = "pending", 0, 0
        job.total_chunks = (
            await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
            or 0
        )
        job.available_at, job.locked_until, job.lease_token, job.error = _utcnow(), None, None, None
        doc.index_status, doc.index_error = "pending", None
        await session.flush()
        return job

    @embedding_operation
    async def index_state(self, document_id: str):
        async with self.factory() as session:
            doc = await session.get(Document, document_id)
            if not doc:
                raise KnowledgeError("文档不存在")
            job = await session.scalar(select(IndexJob).where(IndexJob.document_id == document_id))
            return {
                "document_id": doc.id,
                "upload_status": doc.status,
                "index_status": doc.index_status,
                "embedding_enabled": self.embedder is not None,
                "current_model": self.embedder.profile.model if self.embedder else None,
                "requires_reindex": bool(
                    self.embedder
                    and (doc.index_model, doc.index_revision, doc.index_dimensions)
                    != (
                        self.embedder.profile.model,
                        self.embedder.profile.revision,
                        self.embedder.profile.dimensions,
                    )
                ),
                "error": doc.index_error,
                "process_embedding_usage": dict(
                    self.local_models.embedding_usage
                    if self.local_models
                    else getattr(self.embedder, "usage", {})
                ),
                "job": None
                if not job
                else {
                    "id": job.id,
                    "status": job.status,
                    "attempts": job.attempts,
                    "completed_chunks": job.completed_chunks,
                    "total_chunks": job.total_chunks,
                    "model": job.model,
                    "revision": job.revision,
                    "dimensions": job.dimensions,
                    "error": job.error,
                },
            }

    async def delete_document(self, document_id: str) -> bool:
        # Mark first: a crash/file error leaves a durable tombstone; searches hide it.
        async with self.factory() as session, session.begin():
            doc = await session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if not doc:
                return True
            doc.status = "deleting"
            doc.index_status = "deleting"
        return await self.finish_delete(document_id)

    async def finish_delete(self, document_id: str) -> bool:
        async with self.factory() as session, session.begin():
            doc = await session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if not doc or doc.status != "deleting":
                return True
            if doc.storage_path:
                path = Path(doc.storage_path)
                root = self.settings.upload_dir.resolve()
                # Never unlink an arbitrary DB-provided path or follow a symlink outside uploads.
                if (
                    path.resolve().parent != root
                    or path.is_symlink()
                    or path.suffix
                    not in (".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp")
                    or path.stem != doc.id
                ):
                    doc.index_error = "delete_path_not_managed"
                    return False
                try:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except OSError:
                    doc.index_error = "delete_file_failed"
                    return False
            # Cascades remove chunks, vectors and jobs. File already absent is safe on retry.
            await session.delete(doc)
        return True

    async def cleanup_deletions(self):
        async with self.factory() as session:
            ids = list(
                await session.scalars(
                    select(Document.id).where(Document.status == "deleting").limit(20)
                )
            )
        for document_id in ids:
            await self.finish_delete(document_id)

    @embedding_operation
    async def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        date_from=None,
        date_to=None,
        document_type=None,
    ) -> SearchResponse:
        started = time.perf_counter()
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise KnowledgeError("query 需要 1 到 2000 个字符")
        if mode not in ("keyword", "vector", "hybrid"):
            raise KnowledgeError("mode 只支持 keyword/vector/hybrid")
        top_k = self.settings.rag_top_k if top_k is None else top_k
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise KnowledgeError("top_k 需要为 1 到 10 的整数")
        conditions = document_conditions(
            date_from=date_from, date_to=date_to, document_type=document_type
        )
        if document_ids is not None:
            try:
                if not isinstance(document_ids, list) or len(document_ids) > 100:
                    raise ValueError()
                document_ids = [str(UUID(value)) for value in document_ids]
            except (ValueError, TypeError, AttributeError):
                raise KnowledgeError("document_ids 需要为最多 100 个 UUID 的列表") from None
            if not document_ids:
                return SearchResponse(mode=mode)
        if document_ids is not None:
            conditions.append(Document.id.in_(document_ids))
        async with self.factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            DocumentChunk.id,
                            DocumentChunk.document_id,
                            DocumentChunk.chunk_index,
                            DocumentChunk.content,
                            Document.filename,
                            DocumentChunk.meta,
                            Document.document_date,
                            Document.document_type,
                        )
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(*conditions)
                        .order_by(DocumentChunk.id)
                        .limit(self.settings.rag_max_corpus_chunks + 1)
                    )
                ).all()
            )
        if len(rows) > self.settings.rag_max_corpus_chunks:
            raise KnowledgeError("文档范围过大，请用 document_ids 缩小检索范围")
        if not rows:
            return SearchResponse(mode=mode)
        by_id = {row.id: row for row in rows}
        warnings, lists = [], []
        if mode in ("keyword", "hybrid"):
            lists.append(await asyncio.to_thread(rank_corpus, rows, query, top_k * 4))
        if mode in ("vector", "hybrid"):
            if self.embedder is None:
                if mode == "vector":
                    raise EmbeddingError("Embedding API 未启用或未配置")
                warnings.append("向量检索未启用，使用 BM25")
            else:
                try:
                    candidates = await self.vector_search(query, conditions, top_k * 4)
                    if candidates is None:
                        warnings.append("指定模型尚无完成的索引，未调用 Embedding API")
                    elif (
                        not candidates
                        and mode == "hybrid"
                        and self.settings.rag_hybrid_require_vector_match
                    ):
                        return SearchResponse(
                            mode="hybrid",
                            warnings=["没有达到向量相似度阈值的证据，未放行关键词候选"],
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                        )
                    else:
                        lists.append(candidates)
                except EmbeddingError as exc:
                    if mode == "vector":
                        raise
                    warnings.append(str(exc) + "；已降级为 BM25")
        effective = "hybrid" if len(lists) == 2 else "vector" if mode == "vector" else "keyword"
        ranked = fuse_ranks(lists, top_k * 4) if len(lists) == 2 else lists[0] if lists else []
        budget, seen, hits = self.settings.rag_context_max_bytes, set(), []
        for chunk_id, score in ranked:
            row = by_id.get(chunk_id)
            if row is None:
                continue  # deletion between snapshots never returns an unscoped row
            fingerprint = hashlib.sha256(row.content.strip().encode()).hexdigest()
            if fingerprint in seen:
                continue
            content = trim_utf8(row.content, min(budget, 2500))
            if not content:
                break
            hits.append(
                SearchHit(
                    reference=f"D:{chunk_id}",
                    document_id=row.document_id,
                    chunk_id=chunk_id,
                    chunk_index=row.chunk_index,
                    filename=row.filename[:160],
                    content=content,
                    score=score,
                    source=effective,
                    document_date=row.document_date,
                    document_type=row.document_type,
                    page_number=(row.meta or {}).get("page_number"),
                    section=(row.meta or {}).get("section"),
                )
            )
            budget -= len(content.encode())
            seen.add(fingerprint)
            if len(hits) >= top_k or budget <= 0:
                break
        # Do not serve a document that was deleted while query embedding was in flight.
        if hits:
            async with self.factory() as session:
                live = set(
                    await session.scalars(
                        select(Document.id).where(
                            Document.id.in_([h.document_id for h in hits]), *conditions
                        )
                    )
                )
            hits = [hit for hit in hits if hit.document_id in live]
        return SearchResponse(
            mode=effective,
            hits=hits,
            warnings=warnings,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    @embedding_operation
    async def details(self, document_id, *, offset=0, limit=10, chunk_id=None):
        async with self.factory() as session:
            doc = await session.get(Document, document_id)
            if not doc or doc.status != "ready":
                raise KnowledgeError("文档不存在或正在删除")
            statement = select(DocumentChunk).where(DocumentChunk.document_id == document_id)
            if chunk_id:
                statement = statement.where(DocumentChunk.id == chunk_id)
            chunks = list(
                await session.scalars(
                    statement.order_by(DocumentChunk.chunk_index).offset(offset).limit(limit)
                )
            )
            if chunk_id and not chunks:
                raise KnowledgeError("引用片段不属于该文档或已被删除")
            count = await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
            return {
                "id": doc.id,
                "filename": doc.filename,
                "status": doc.status,
                "index_status": doc.index_status,
                "document_date": doc.document_date.isoformat() if doc.document_date else None,
                "requires_reindex": bool(
                    self.embedder
                    and (doc.index_model, doc.index_revision, doc.index_dimensions)
                    != (
                        self.embedder.profile.model,
                        self.embedder.profile.revision,
                        self.embedder.profile.dimensions,
                    )
                ),
                "document_type": doc.document_type,
                "created_at": doc.created_at.isoformat(),
                "content_hash": doc.content_hash,
                "metadata": doc.meta or {},
                "chunk_count": count,
                "offset": offset,
                "has_more": not chunk_id and offset + len(chunks) < count,
                "chunks": [
                    {
                        "id": chunk.id,
                        "reference": "D:" + chunk.id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "page_number": (chunk.meta or {}).get("page_number"),
                        "section": (chunk.meta or {}).get("section"),
                    }
                    for chunk in chunks
                ],
            }

    @embedding_operation
    async def vector_search(self, query: str, conditions, top_k: int):
        if self.embedder is None:
            raise EmbeddingError("Embedding API 未启用或未配置")
        from pgvector.sqlalchemy import Vector

        from app.infrastructure.database.pg_models import ChunkEmbedding
        from app.knowledge.embedding import validate_vectors

        profile = self.embedder.profile
        # Avoid paying to embed queries when this profile has no ready vectors in scope.
        async with self.factory() as session:
            available = await session.scalar(
                select(ChunkEmbedding.id)
                .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    *conditions,
                    ChunkEmbedding.model == profile.model,
                    ChunkEmbedding.revision == profile.revision,
                    ChunkEmbedding.dimensions == profile.dimensions,
                    Document.index_status == "ready",
                    Document.index_model == profile.model,
                    Document.index_revision == profile.revision,
                    Document.index_dimensions == profile.dimensions,
                )
                .limit(1)
            )
        if available is None:
            return None
        vector = validate_vectors(await self.embedder.embed([query]), 1, profile.dimensions)[0]
        distance = ChunkEmbedding.embedding.op("OPERATOR(public.<=>)", return_type=Float)(
            cast(vector, Vector())
        )
        try:
            async with self.factory() as session:
                rows = (
                    await session.execute(
                        select(ChunkEmbedding.chunk_id, distance.label("distance"))
                        .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(
                            *conditions,
                            ChunkEmbedding.model == profile.model,
                            ChunkEmbedding.revision == profile.revision,
                            ChunkEmbedding.dimensions == profile.dimensions,
                            Document.index_status == "ready",
                            Document.index_model == profile.model,
                            Document.index_revision == profile.revision,
                            Document.index_dimensions == profile.dimensions,
                        )
                        .order_by(distance, ChunkEmbedding.chunk_id)
                        .limit(top_k)
                    )
                ).all()
        except SQLAlchemyError:
            raise EmbeddingError("vector_storage_unavailable", retryable=True) from None
        threshold = self.settings.min_similarity_for(profile.model)
        return [
            (row.chunk_id, 1 - row.distance)
            for row in rows
            if row.distance is not None and 1 - row.distance >= threshold
        ]
