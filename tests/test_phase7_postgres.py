"""Real local PG fixture lifecycle, synthetic embeddings, no external API."""

import os
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.config import Settings
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import Document, DocumentChunk, IndexJob
from app.infrastructure.database.pg_models import ChunkEmbedding
from app.knowledge.types import EmbeddingProfile
from eval.phase7_fixtures import DocumentFixture, ScopedKnowledge
from tests.conftest import run

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


def test_evaluation_indexes_only_own_documents_and_cleanup_preserves_foreign_document():
    async def scenario():
        sha = uuid.uuid4().hex
        settings = Settings(
            _env_file=None,
            database_url=load_config()["DATABASE_URL"],
            embedding_enabled=False,
            embedding_api_base="https://synthetic.invalid/v1",
            embedding_model="synthetic-phase7",
            embedding_dimensions=4,
        )

        class Synthetic:
            profile = EmbeddingProfile.create(
                settings.embedding_api_base, settings.embedding_model, 4, "phase7-" + sha
            )
            calls = 0

            async def embed(self, texts):
                self.calls += 1
                return [[1.0, 0.0, 0.0, 0.0] for text in texts]

        embedder = Synthetic()
        fixture = DocumentFixture(settings, embedder, sha)
        foreign = None
        try:
            async with fixture.factory() as session, session.begin():
                doc = Document(
                    filename="synthetic foreign sentinel",
                    status="ready",
                    meta={"phase7_test_sentinel": sha},
                )
                session.add(doc)
                await session.flush()
                foreign = doc.id
                session.add(
                    DocumentChunk(
                        document_id=foreign, chunk_index=0, content="配送服务承诺 外部哨兵"
                    )
                )
            await fixture.seed()
            assert len(fixture.owned) == 15 and embedder.calls > 0
            for doc_id in fixture.owned:
                assert (await fixture.service.index_state(doc_id))["index_status"] == "ready"
            scoped = ScopedKnowledge(fixture.service, fixture.owned, "keyword")
            result = await scoped.search("配送服务约定")
            assert result.hits and all(hit.document_id in fixture.owned for hit in result.hits)
            with pytest.raises(ValueError):
                await scoped.search("配送", document_ids=[foreign])
            async with fixture.factory() as session:
                owned_chunks = select(DocumentChunk.id).where(
                    DocumentChunk.document_id.in_(fixture.owned)
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ChunkEmbedding)
                        .where(ChunkEmbedding.chunk_id.in_(owned_chunks))
                    )
                    > 0
                )
            await fixture.close()
            async with fixture.factory() as session:
                assert await session.get(Document, foreign) is not None
                assert not list(
                    await session.scalars(select(Document.id).where(Document.id.in_(fixture.owned)))
                )
                assert not list(
                    await session.scalars(
                        select(IndexJob.id).where(IndexJob.document_id.in_(fixture.owned))
                    )
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ChunkEmbedding)
                        .where(
                            ChunkEmbedding.model == "synthetic-phase7",
                            ChunkEmbedding.revision == embedder.profile.revision,
                        )
                    )
                    == 0
                )
        finally:
            await fixture.close()
            if foreign:
                async with fixture.factory() as session, session.begin():
                    doc = await session.get(Document, foreign)
                    if doc and (doc.meta or {}).get("phase7_test_sentinel") == sha:
                        await session.execute(delete(Document).where(Document.id == foreign))
            await fixture.engine.dispose()

    run(scenario())
