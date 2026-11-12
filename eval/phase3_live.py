"""Explicitly approved BGE-M3 integration using ONLY the synthetic DOCS/CASES.

Hard transport limits apply to every HTTP attempt, including automatic retries.
The application's EMBEDDING_ENABLED flag is never changed on disk.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.session import init_engine
from app.knowledge.embedding import APIEmbedder
from app.knowledge.service import KnowledgeService
from app.knowledge.types import EmbeddingError
from app.knowledge.worker import IndexWorker
from eval.phase3_offline import CASES, DOCS


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner=None,
        *,
        max_requests=50,
        max_characters=50000,
        ledger_path=None,
        allowed_inputs=None,
    ):
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.max_requests, self.max_characters = max_requests, max_characters
        self.requests, self.characters = 0, 0
        self.ledger_path, self.allowed_inputs = ledger_path, allowed_inputs
        if ledger_path and ledger_path.exists():
            previous = json.loads(ledger_path.read_text(encoding="utf-8"))
            if previous["limits"] != {"requests": max_requests, "characters": max_characters}:
                raise ValueError("Embedding ledger limits do not match the authorized run")
            self.requests, self.characters = previous["requests"], previous["characters"]
            if (
                type(self.requests) is not int
                or type(self.characters) is not int
                or min(self.requests, self.characters) < 0
            ):
                raise ValueError("Invalid embedding ledger")
        self.lock = asyncio.Lock()

    async def handle_async_request(self, request):
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.port not in (None, 443)
            or request.url.host != "api.siliconflow.cn"
            or request.url.path != "/v1/embeddings"
        ):
            raise EmbeddingError("unapproved_embedding_destination")
        payload = json.loads(request.content)
        if payload.get("model") != "BAAI/bge-m3":
            raise EmbeddingError("unapproved_embedding_model")
        inputs = payload["input"]
        if not isinstance(inputs, list) or any(not isinstance(value, str) for value in inputs):
            raise EmbeddingError("embedding_input_invalid")
        if self.allowed_inputs is not None and any(
            value not in self.allowed_inputs for value in inputs
        ):
            raise EmbeddingError("embedding_input_not_in_synthetic_allowlist")
        characters = sum(len(value) for value in inputs)
        async with self.lock:
            if (
                self.requests >= self.max_requests
                or self.characters + characters > self.max_characters
            ):
                raise EmbeddingError("embedding_budget_exceeded")
            self.requests += 1
            self.characters += characters
            if self.ledger_path:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                self.ledger_path.write_text(
                    json.dumps(
                        {
                            "requests": self.requests,
                            "characters": self.characters,
                            "limits": {
                                "requests": self.max_requests,
                                "characters": self.max_characters,
                            },
                        }
                    ),
                    encoding="utf-8",
                )
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


async def evaluate(args):
    original = Settings()
    if (
        original.embedding_api_base.rstrip("/") != "https://api.siliconflow.cn/v1"
        or original.embedding_model != "BAAI/bge-m3"
        or original.embedding_dimensions != 1024
    ):
        raise ValueError(
            "Live eval requires the approved SiliconFlow BAAI/bge-m3 / 1024 configuration"
        )
    settings = original.model_copy(update={"embedding_enabled": True, "embedding_batch_size": 4})
    engine = init_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    budget = BudgetTransport()
    embedder = APIEmbedder(settings, transport=budget)
    service = KnowledgeService(factory, settings, embedder)
    doc_ids, results, indexes = {}, [], []
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "provider": "SiliconFlow",
        "model": "BAAI/bge-m3",
        "dimensions": 1024,
        "data_scope": "Synthetic DOCS/CASES from eval.phase3_offline only",
        "limits": {"http_requests": 50, "input_characters": 50000},
        "persistent_embedding_flag_changed": False,
    }
    try:
        await service.check_schema()
        for key, (filename, content) in DOCS.items():
            async with factory() as session, session.begin():
                document = Document(
                    filename="live-eval-" + filename, status="ready", index_status="not_indexed"
                )
                session.add(document)
                await session.flush()
                doc_ids[key] = document.id
                session.add(DocumentChunk(document_id=document.id, chunk_index=0, content=content))
                await session.flush()
                await service.enqueue(session, document.id)
        # Scope claims as well as searches; never run a real user's pending job.
        worker = IndexWorker(service, document_ids=list(doc_ids.values()))
        for _ in range(len(doc_ids)):
            await worker.run_once()
        for document_id in doc_ids.values():
            state = await service.index_state(document_id)
            indexes.append(state)
            if state["index_status"] != "ready":
                raise EmbeddingError("live_index_failed: " + str(state["error"]))
        for case_id, query, expected in CASES:
            for mode in ("keyword", "vector", "hybrid"):
                response = await service.search(
                    query, mode=mode, document_ids=list(doc_ids.values()), top_k=3
                )
                ids = [item.document_id for item in response.hits]
                rank = (
                    ids.index(doc_ids[expected]) + 1
                    if expected and doc_ids[expected] in ids
                    else None
                )
                results.append(
                    {
                        "id": case_id,
                        "query": query,
                        "mode": mode,
                        "expected": expected,
                        "hit_at_1": bool(expected and ids and ids[0] == doc_ids[expected]),
                        "hit_at_3": bool(rank) if expected else not ids,
                        "reciprocal_rank": 1 / rank if rank else 0,
                        "returned_count": len(ids),
                        "warnings": response.warnings,
                        "elapsed_ms": response.elapsed_ms,
                    }
                )
        metrics = {}
        for mode in ("keyword", "vector", "hybrid"):
            group = [row for row in results if row["mode"] == mode]
            answerable = [row for row in group if row["expected"]]
            unanswerable = [row for row in group if not row["expected"]]
            metrics[mode] = {
                "hit_at_1": sum(row["hit_at_1"] for row in answerable) / len(answerable),
                "hit_at_3": sum(row["hit_at_3"] for row in answerable) / len(answerable),
                "mrr": sum(row["reciprocal_rank"] for row in answerable) / len(answerable),
                "no_answer_accuracy": sum(row["hit_at_3"] for row in unanswerable)
                / len(unanswerable),
                "average_ms": round(sum(row["elapsed_ms"] for row in group) / len(group), 2),
            }
        report.update(status="completed", metrics=metrics, results=results)
    except Exception as exc:
        report.update(
            status="failed",
            error=str(exc) if isinstance(exc, EmbeddingError) else type(exc).__name__,
            results=results,
        )
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.id.in_(doc_ids.values())))
        await embedder.aclose()
        await engine.dispose()
        report.update(
            http_requests=budget.requests,
            input_characters=budget.characters,
            usage=embedder.usage,
            synthetic_rows_cleaned_up=True,
            indexed_documents=len(indexes),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ("status", "http_requests", "input_characters", "usage")},
            ensure_ascii=True,
        )
    )
    return 0 if report["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/phase4/bge-m3-live.json"))
    return asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
