"""Synthetic retrieval acceptance. Tests the pipeline, NOT real embedding quality.

No external model requests, packages, images or model weights. Optional explicit
restart touches only the existing dedicated project PostgreSQL container.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.infrastructure.database.manage import ROOT, load_config
from app.infrastructure.database.models import Document, DocumentChunk, IndexJob
from app.infrastructure.database.session import init_engine
from app.knowledge.service import KnowledgeService
from app.knowledge.types import EmbeddingProfile
from app.knowledge.worker import IndexWorker

DOCS = {
    "sales": (
        "销售口径.md",
        "净销售额仅计入 paid 和 completed 订单；排除已退款、已取消订单与测试账号。金额以分存储。",
    ),
    "refund": (
        "退款规则.md",
        "退款率等于已退款订单数除以已支付订单数。不能用包含被驳回申请的 refund_requests 作为分子。",
    ),
    "delivery": ("配送政策.md", "配送时效为发货后三个工作日，偏远地区需要五个工作日。"),
}
CASES = [
    ("sales_exact", "净销售额", "sales"),
    ("sales_synonym", "实收收入如何计算", "sales"),
    ("sales_exclusion", "净销售额排除哪些订单", "sales"),
    ("sales_units", "金额以什么单位存储", "sales"),
    ("refund_exact", "退款率如何定义", "refund"),
    ("refund_synonym", "退货比例是什么", "refund"),
    ("refund_requests", "refund_requests 能作为分子吗", "refund"),
    ("delivery_exact", "配送时效", "delivery"),
    ("delivery_synonym", "多久送达", "delivery"),
    ("delivery_remote", "偏远地区需要几个工作日", "delivery"),
    ("unanswerable_space", "火星轨道参数", None),
    ("unanswerable_inventory", "仓库库存盘点公式", None),
]


class SyntheticEmbedder:
    def __init__(self, run_id):
        self.profile = EmbeddingProfile.create(
            "synthetic://acceptance/" + run_id, "synthetic-not-a-real-model", 4, "v1"
        )
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1

        def encode(value):
            if any(word in value for word in ("净销售额", "收入", "营收", "金额", "实收")):
                return [1.0, 0.0, 0.0, 0.0]
            if any(word in value for word in ("退款率", "退货比例", "refund_requests")):
                return [0.0, 1.0, 0.0, 0.0]
            if any(word in value for word in ("配送", "多久送达", "偏远地区")):
                return [0.0, 0.0, 1.0, 0.0]
            return [0.0, 0.0, 0.0, 1.0]

        return [encode(value) for value in texts]


async def restart_database(factory):
    container = "data-analyst-phase2-postgres"

    def restart():
        details = json.loads(
            subprocess.run(
                ["docker", "inspect", container], check=True, capture_output=True, text=True
            ).stdout
        )[0]
        if details["Config"]["Image"] != "pgvector/pgvector:0.8.7-pg16" or not any(
            item.get("Name") == "data-analyst-phase2-postgres-data" for item in details["Mounts"]
        ):
            raise RuntimeError("Unexpected database container; refusing restart")
        subprocess.run(["docker", "restart", container], check=True, capture_output=True)

    await asyncio.to_thread(restart)
    deadline = time.monotonic() + 45
    while True:
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.5)


async def evaluate(args):
    config = load_config()
    settings = Settings(
        _env_file=None, database_url=config["DATABASE_URL"], embedding_enabled=False
    )
    engine = init_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    embedder = SyntheticEmbedder(uuid.uuid4().hex)
    service = KnowledgeService(factory, settings, embedder)
    doc_ids, results = {}, []
    try:
        await service.check_schema()
        for key, (filename, content) in DOCS.items():
            async with factory() as session, session.begin():
                document = Document(filename=filename, status="ready", index_status="not_indexed")
                session.add(document)
                await session.flush()
                doc_ids[key] = document.id
                session.add(DocumentChunk(document_id=document.id, chunk_index=0, content=content))
                await session.flush()
                await service.enqueue(session, document.id)
        if args.restart_database:
            await restart_database(factory)
        async with factory() as session:
            pending = list(
                await session.scalars(
                    select(IndexJob.document_id).where(
                        IndexJob.document_id.in_(doc_ids.values()), IndexJob.status == "pending"
                    )
                )
            )
        assert len(pending) == len(DOCS)
        worker = IndexWorker(service)
        while await worker.run_once():
            pass
        for document_id in doc_ids.values():
            assert (await service.index_state(document_id))["index_status"] == "ready"
        for case_id, query, expected in CASES:
            for mode in ("keyword", "vector", "hybrid"):
                response = await service.search(
                    query, mode=mode, document_ids=list(doc_ids.values()), top_k=3
                )
                ranks = [hit.document_id for hit in response.hits]
                rank = (
                    ranks.index(doc_ids[expected]) + 1
                    if expected and doc_ids[expected] in ranks
                    else None
                )
                correct = bool(rank) if expected else not ranks
                results.append(
                    {
                        "id": case_id,
                        "query": query,
                        "mode": mode,
                        "expected": expected,
                        "hit_at_3": correct,
                        "reciprocal_rank": 1 / rank if rank else 0,
                        "references": [hit.reference for hit in response.hits],
                        "elapsed_ms": response.elapsed_ms,
                    }
                )
        metrics = {}
        for mode in ("keyword", "vector", "hybrid"):
            subset = [item for item in results if item["mode"] == mode]
            answerable = [item for item in subset if item["expected"] is not None]
            no_answer = [item for item in subset if item["expected"] is None]
            metrics[mode] = {
                "hit_at_3": sum(item["hit_at_3"] for item in answerable) / len(answerable),
                "mrr": sum(item["reciprocal_rank"] for item in answerable) / len(answerable),
                "no_answer_accuracy": sum(item["hit_at_3"] for item in no_answer) / len(no_answer),
                "average_ms": round(sum(item["elapsed_ms"] for item in subset) / len(subset), 2),
            }
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "kind": "Synthetic pipeline acceptance; not real model retrieval quality",
            "external_model_calls": 0,
            "synthetic_embed_calls": embedder.calls,
            "database_restarted": args.restart_database,
            "pending_jobs_recovered": len(pending),
            "document_count": len(doc_ids),
            "question_count": len(CASES),
            "metrics": metrics,
            "results": results,
        }
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.id.in_(doc_ids.values())))
        await engine.dispose()
    report["synthetic_rows_cleaned_up"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"metrics": metrics, "external_model_calls": 0, "pending_jobs_recovered": len(pending)},
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart-database", action="store_true")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/phase3/offline-retrieval.json")
    return asyncio.run(evaluate(parser.parse_args()))


if __name__ == "__main__":
    main()
