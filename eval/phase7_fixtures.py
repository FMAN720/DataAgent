"""Own synthetic SQLite data and UUID-scoped PG documents, isolated from user uploads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.analytics.prompts import build_system_prompt
from app.config import Settings
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.datasets.parser import parse_table
from app.demo import build_demo_db
from app.etl.pipeline import ETLPipeline
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.session import init_engine
from app.knowledge.service import KnowledgeService
from app.knowledge.types import EmbeddingProfile
from app.knowledge.worker import IndexWorker
from eval.phase7_cases import CASE_SHA256, CASES

ROOT = Path(__file__).resolve().parents[1]
DOC_ROOT = ROOT / "app/demo/documents"
DOCS = json.loads((DOC_ROOT / "manifest.json").read_text(encoding="utf-8"))
CSV = b"region_code,amount,external_id\nEAST,12.30,001\nNORTH,0.10,002\nSOUTH,2.50,003\nEAST,,004\n"


def fingerprint():
    material = {
        "cases": CASE_SHA256,
        "csv": hashlib.sha256(CSV).hexdigest(),
        "seed": hashlib.sha256((ROOT / "app/demo/seed.py").read_bytes()).hexdigest(),
        "docs": {
            item["id"]: hashlib.sha256((DOC_ROOT / item["file"]).read_bytes()).hexdigest()
            for item in DOCS
        },
        "parsers": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("app/etl/parser.py", "app/etl/chunker.py", "app/datasets/parser.py")
        },
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    database = work / "synthetic.db"
    parsed = parse_table(CSV, "合成预算补充表.csv", Settings(_env_file=None))
    if not database.exists():
        build_demo_db(database)
        with sqlite3.connect(database) as conn:
            conn.execute("""CREATE TABLE uploaded_budget (
                _row_number INTEGER NOT NULL,
                region_code TEXT, -- 区域编码，可关联 regions
                amount NUMERIC, -- 单位：元；空值不代表零
                external_id TEXT -- 原始标识符，保留前导零
            )""")
            conn.executemany(
                "INSERT INTO uploaded_budget VALUES (?,?,?,?)",
                [
                    (number, *[str(value) if value is not None else None for value in values])
                    for number, values in parsed.rows
                ],
            )
    db = AnalyticsDatabase("sqlite:///" + database.as_posix())
    try:
        import threading

        gold = {
            item["id"]: [
                list(row) for row in db._execute_sync(item["gold_sql"], threading.Event()).rows
            ]
            for item in CASES
            if item["gold_sql"]
        }
    finally:
        db.dispose()
    manifest = {
        "fingerprint": fingerprint(),
        "case_sha256": CASE_SHA256,
        "cases": CASES,
        "gold": gold,
        "synthetic_database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "documents": DOCS,
        "dataset_columns": parsed.columns,
        "scope": "seeded demo data, 15 committed synthetic markdown files, synthetic CSV only; no user uploads",
    }
    target = out / "prepared.json"
    if target.exists():
        old = json.loads(target.read_text(encoding="utf-8"))
        if (
            old["fingerprint"] != manifest["fingerprint"]
            or old["synthetic_database_sha256"] != manifest["synthetic_database_sha256"]
        ):
            raise ValueError(
                "Prepared evaluation changed; preserve old results and create an explicit new version"
            )
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, database


class DatasetFixture:
    def __init__(self, columns):
        self.columns = columns

    async def list(self, **kwargs):
        if kwargs.get("query") and kwargs["query"] not in "合成预算补充表.csv":
            return []
        return [
            {
                "id": "phase7-synthetic-budget",
                "filename": "合成预算补充表.csv",
                "display_name": "合成预算补充表",
                "table": "uploaded_budget",
                "row_count": 4,
                "columns": self.columns,
            }
        ]


class ScopedKnowledge:
    def __init__(self, service, ids, mode):
        self.service, self.ids, self.mode = service, set(ids), mode

    async def search(self, query, **kwargs):
        requested = kwargs.pop("document_ids", None)
        if requested is not None and not set(requested) <= self.ids:
            raise ValueError("Evaluation cannot read foreign documents")
        return await self.service.search(
            query,
            document_ids=sorted(self.ids if requested is None else requested),
            mode=self.mode,
            **kwargs,
        )


class DocumentFixture:
    def __init__(self, settings, embedder, fixture_sha):
        self.settings = settings.model_copy(update={"embedding_revision": "phase7-" + fixture_sha})
        self.engine = init_engine(settings.database_url, poolclass=NullPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.service = KnowledgeService(self.factory, self.settings, embedder)
        self.sha = fixture_sha
        self.ids = {
            item["id"]: str(
                uuid.uuid5(uuid.NAMESPACE_URL, "phase7:" + fixture_sha + ":" + item["id"])
            )
            for item in DOCS
        }
        self.owned = set()

    async def seed(self):
        # Embedder must use the same isolated profile, so the ordinary worker cannot claim it.
        expected = EmbeddingProfile.create(
            self.settings.embedding_api_base,
            self.settings.embedding_model,
            self.settings.embedding_dimensions,
            self.settings.embedding_revision,
        )
        if self.service.embedder.profile != expected:
            raise ValueError("Evaluation embedding profile differs")
        pipeline = ETLPipeline()
        async with self.factory() as session, session.begin():
            for item in DOCS:
                doc_id = self.ids[item["id"]]
                existing = await session.get(Document, doc_id)
                if existing:
                    if (existing.meta or {}).get("phase7_fingerprint") != self.sha:
                        raise ValueError("Document ownership conflict")
                    self.owned.add(doc_id)
                    await self.service.enqueue(session, doc_id, retry=True)
                    continue
                parsed = await pipeline.run_bytes(
                    (DOC_ROOT / item["file"]).read_bytes(), item["file"], "text/markdown"
                )
                doc = Document(
                    id=doc_id,
                    filename="第七阶段合成评测-" + item["file"],
                    status="ready",
                    document_date=date.fromisoformat(item["date"]) if item["date"] else None,
                    document_type=item["type"],
                    meta={"phase7_fingerprint": self.sha, "chunk_count": len(parsed.chunks)},
                )
                session.add(doc)
                await session.flush()
                self.owned.add(doc_id)
                for index, content in enumerate(parsed.chunks):
                    session.add(
                        DocumentChunk(
                            document_id=doc_id,
                            chunk_index=index,
                            content=content,
                            meta=parsed.chunk_metadata[index] if parsed.chunk_metadata else {},
                        )
                    )
                await session.flush()
                await self.service.enqueue(session, doc_id)
        worker = IndexWorker(self.service, document_ids=self.owned)
        while await worker.run_once():
            pass
        for doc_id in self.owned:
            state = await self.service.index_state(doc_id)
            if state["index_status"] != "ready":
                raise ValueError("Synthetic vector index not ready: " + str(state["error"]))

    async def close(self):
        try:
            async with self.factory() as session, session.begin():
                for doc_id in self.owned:
                    doc = await session.get(Document, doc_id)
                    if doc and (doc.meta or {}).get("phase7_fingerprint") != self.sha:
                        raise ValueError("Cleanup ownership mismatch")
                await session.execute(delete(Document).where(Document.id.in_(self.owned)))
        finally:
            await self.engine.dispose()


def build_evaluation_agent(llm, database, manifest, knowledge, variant):
    db = AnalyticsDatabase("sqlite:///" + database.as_posix(), max_rows=100, timeout_seconds=10)
    registry = ToolRegistry(timeout_seconds=15)
    for tool in build_analytics_tools(db, GlossaryStore.from_file(ROOT / "app/demo/glossary.md")):
        registry.register(tool)

    async def close():
        db.dispose()

    agent = AgentOrchestrator(
        llm,
        registry,
        InMemorySessionStore(),
        system_prompt=build_system_prompt("sqlite", "2025-12-31"),
        max_steps=8,
        enable_reflection=variant != "no_reflection",
        close_callback=close,
    )
    agent.enable_dataset_tools(DatasetFixture(manifest["dataset_columns"]))
    agent.enable_document_search(
        ScopedKnowledge(
            knowledge.service, knowledge.owned, "keyword" if variant == "keyword" else "hybrid"
        )
    )
    return agent
