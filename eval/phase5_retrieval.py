"""Synthetic corpus evaluation; --live explicitly spends the approved API budget.

Question embeddings are cached within this run. Thresholds are replayed over real
PostgreSQL cosine scores, without charging the same query again. No user corpus
is read: all documents, candidates, and worker claims are scoped to newly created
synthetic IDs. An on-disk charge ledger persists across failed/restarted runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.etl.pipeline import ETLPipeline
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.session import init_engine
from app.knowledge.embedding import APIEmbedder
from app.knowledge.service import KnowledgeService, document_conditions, fuse_ranks, rank_corpus
from app.knowledge.types import EmbeddingError
from app.knowledge.worker import IndexWorker
from eval.phase3_live import BudgetTransport
from eval.phase5_cases import cases, confirmation_cases, corpus

THRESHOLDS = (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8)


class CachedEmbedder:
    def __init__(self, delegate):
        self.delegate, self.profile, self.cache = delegate, delegate.profile, {}

    async def embed(self, texts):
        missing = list(dict.fromkeys(value for value in texts if value not in self.cache))
        if missing:
            vectors = await self.delegate.embed(missing)
            self.cache.update(zip(missing, vectors))
        return [self.cache[value] for value in texts]


def metrics(rows):
    answerable = [row for row in rows if row["expected"]]
    negative = [row for row in rows if not row["expected"]]
    hit5 = sum(row["rank"] is not None for row in answerable) / len(answerable)
    rejected = sum(not row["returned"] for row in negative) / len(negative)
    return {
        "answerable": len(answerable),
        "unanswerable": len(negative),
        "hit_at_1": sum(row["rank"] == 1 for row in answerable) / len(answerable),
        "hit_at_5": hit5,
        "mrr": sum(1 / row["rank"] if row["rank"] else 0 for row in answerable) / len(answerable),
        "no_answer_accuracy": rejected,
        "balanced_score": (hit5 + rejected) / 2,
    }


def replay(records, chunk_documents, threshold, mode, split):
    output = []
    for case in records:
        if case["split"] != split:
            continue
        keyword = case["keyword"][:20]
        vector = [(chunk, score) for chunk, score in case["vector"] if score >= threshold][:20]
        ranked = (
            keyword
            if mode == "keyword"
            else vector
            if mode == "vector"
            else []
            if mode == "hybrid_gated" and not vector
            else fuse_ranks([keyword, vector], 20)
        )
        ids = [chunk_documents[chunk] for chunk, _ in ranked[:5]]
        output.append(
            {
                "id": case["id"],
                "query": case["query"],
                "expected": case["expected"],
                "rank": ids.index(case["expected"]) + 1 if case["expected"] in ids else None,
                "returned": ids,
                "maximum_similarity": case["vector"][0][1] if case["vector"] else None,
            }
        )
    return output


async def evaluate(args):
    values = corpus()
    questions = confirmation_cases() if args.confirmation else cases()
    prepared = [
        (
            item,
            await ETLPipeline().run_bytes(item["content"].encode(), item["file"], "text/markdown"),
        )
        for item in values
    ]
    allowed = frozenset(
        [question["query"] for question in questions]
        + [chunk for _, etl in prepared for chunk in etl.chunks]
    )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "documents": len(values),
        "questions": len(questions),
        "scope": "Committed synthetic app/demo/documents and eval/phase5_cases only",
        "model_calls_chat": 0,
        "calibration_questions": 0 if args.confirmation else 30,
        "holdout_questions": len(questions) if args.confirmation else 30,
        "confirmation_after_gate_change": args.confirmation,
        "persistent_embedding_flag_changed": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if not args.live:
        report.update(
            status="offline_prepared",
            characters_in_unique_allowlist=sum(map(len, allowed)),
            chunks=sum(len(etl.chunks) for _, etl in prepared),
            queries=questions,
            external_http_requests=0,
        )
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Synthetic evaluation prepared; no external requests")
        return 0
    original = Settings()
    if (
        original.embedding_api_base.rstrip("/") != "https://api.siliconflow.cn/v1"
        or original.embedding_model != "BAAI/bge-m3"
        or original.embedding_dimensions != 1024
    ):
        raise ValueError("Approved evaluation requires SiliconFlow BAAI/bge-m3, 1024 dimensions")
    settings = original.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_batch_size": 16,
            "rag_min_similarity": -1,
            "rag_bge_m3_min_similarity": -1,
            "rag_context_max_bytes": 16000,
        }
    )
    budget = BudgetTransport(
        max_requests=120,
        max_characters=100000,
        ledger_path=args.out.parent / "embedding-budget.json",
        allowed_inputs=allowed,
    )
    delegate = APIEmbedder(settings, transport=budget)
    embedder = CachedEmbedder(delegate)
    engine = init_engine(load_config()["DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service, owned, mapping = KnowledgeService(factory, settings, embedder), [], {}
    records, chunks = [], []
    try:
        # Batched real embeddings for this allowlist only; later service calls reuse them.
        await embedder.embed(sorted(allowed))
        for item, etl in prepared:
            async with factory() as session, session.begin():
                doc = Document(
                    id=str(uuid.uuid4()),
                    filename="phase5-eval-" + item["file"],
                    status="ready",
                    document_date=date.fromisoformat(item["date"]) if item["date"] else None,
                    document_type=item["type"],
                    meta={"synthetic_eval": True, "chunk_count": len(etl.chunks)},
                )
                session.add(doc)
                await session.flush()
                owned.append(doc.id)
                mapping[doc.id] = item["id"]
                for number, content in enumerate(etl.chunks):
                    session.add(
                        DocumentChunk(
                            document_id=doc.id,
                            chunk_index=number,
                            content=content,
                            meta=etl.chunk_metadata[number],
                        )
                    )
                await session.flush()
                await service.enqueue(session, doc.id)
        worker = IndexWorker(service, document_ids=owned)
        for _ in owned:
            await worker.run_once()
        for document_id in owned:
            if (await service.index_state(document_id))["index_status"] != "ready":
                raise EmbeddingError("synthetic_index_failed")
        conditions = [
            *document_conditions(date_from="2025-01-01", date_to="2025-12-31"),
            Document.id.in_(owned),
        ]
        async with factory() as session:
            rows = (
                await session.execute(
                    select(DocumentChunk.id, DocumentChunk.content, DocumentChunk.document_id)
                    .join(Document, Document.id == DocumentChunk.document_id)
                    .where(*conditions)
                )
            ).all()
        chunks = [SimpleNamespace(id=row.id, content=row.content) for row in rows]
        chunk_documents = {row.id: mapping[row.document_id] for row in rows}
        for question in questions:
            records.append(
                {
                    **question,
                    "keyword": rank_corpus(chunks, question["query"], 50),
                    "vector": await service.vector_search(
                        question["query"], conditions, max(50, len(chunks))
                    ),
                }
            )
        # Select on calibration ONLY, before computing or inspecting holdout metrics.
        calibration = (
            []
            if args.confirmation
            else [
                {
                    "threshold": threshold,
                    **metrics(replay(records, chunk_documents, threshold, "vector", "calibration")),
                }
                for threshold in THRESHOLDS
            ]
        )
        chosen = (
            0.6
            if args.confirmation
            else max(
                calibration, key=lambda row: (row["balanced_score"], row["mrr"], -row["threshold"])
            )["threshold"]
        )
        comparison = {}
        detailed = {}
        for mode in ("keyword", "vector", "hybrid", "hybrid_gated"):
            for threshold in sorted({0.35, chosen}):
                key = f"{mode}@{threshold}"
                detailed[key] = replay(records, chunk_documents, threshold, mode, "holdout")
                comparison[key] = metrics(detailed[key])
        # Verify replay against actual public search for every holdout vector query.
        settings.rag_min_similarity = chosen
        settings.rag_bge_m3_min_similarity = chosen
        verified = 0
        expected_by_id = {row["id"]: row for row in detailed[f"vector@{chosen}"]}
        for question in questions:
            if question["split"] != "holdout":
                continue
            for mode in ("vector", "hybrid") if args.confirmation else ("vector",):
                result = await service.search(
                    question["query"],
                    mode=mode,
                    top_k=5,
                    document_ids=owned,
                    date_from="2025-01-01",
                    date_to="2025-12-31",
                )
                actual = [mapping[hit.document_id] for hit in result.hits]
                reference = (
                    expected_by_id[question["id"]]
                    if mode == "vector"
                    else next(
                        row
                        for row in detailed[f"hybrid_gated@{chosen}"]
                        if row["id"] == question["id"]
                    )
                )
                if actual != reference["returned"]:
                    raise RuntimeError("retrieval_replay_mismatch")
                verified += 1
        report.update(
            status="completed",
            selected_vector_threshold=chosen,
            threshold_applied_to_persistent_config=args.confirmation,
            calibration=calibration,
            holdout=comparison,
            results=detailed,
            verified_service_queries=verified,
            timing_note="Queries reuse in-memory real embeddings; not an uncached end-to-end latency benchmark",
            relevance_note="Expected document identity only; this is not an LLM final-answer or causal-reasoning evaluation",
        )
    except Exception as exc:
        report.update(
            status="failed",
            error=str(exc) if isinstance(exc, EmbeddingError) else type(exc).__name__,
        )
    finally:
        try:
            async with factory() as session, session.begin():
                await session.execute(delete(Document).where(Document.id.in_(owned)))
            report["synthetic_rows_cleaned_up"] = True
        finally:
            await delegate.aclose()
            await engine.dispose()
            report.update(
                http_requests_total=budget.requests,
                input_characters_total=budget.characters,
                run_usage=delegate.usage,
                limits={"http_requests": 120, "input_characters": 100000},
            )
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("status", "http_requests_total", "input_characters_total")
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Spend an explicitly approved synthetic API budget"
    )
    parser.add_argument(
        "--confirmation",
        action="store_true",
        help="Fresh 20-question validation of the locked threshold and gate; no threshold tuning",
    )
    parser.add_argument("--out", type=Path, default=Path("reports/phase5/retrieval.json"))
    return asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
