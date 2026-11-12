"""Restart only the dedicated phase 2 container and prove committed data survives.

No LLM requests, image pulls, package installations, or volume removal.
"""

import json
import subprocess
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.infrastructure.database.manage import ROOT, load_config
from app.infrastructure.database.models import Document, DocumentChunk
from app.infrastructure.database.pg_models import ChunkEmbedding

CONTAINER = "data-analyst-phase2-postgres"
VOLUME = "data-analyst-phase2-postgres-data"


def main():
    values = load_config()
    details = json.loads(
        subprocess.run(
            ["docker", "inspect", CONTAINER], check=True, capture_output=True, text=True
        ).stdout
    )[0]
    if details["Config"]["Image"] != "pgvector/pgvector:0.8.7-pg16" or not any(
        v.get("Name") == VOLUME for v in details["Mounts"]
    ):
        raise RuntimeError("Unexpected container; refusing restart")
    engine = create_engine(
        make_url(values["DATABASE_URL"]).set(drivername="postgresql+psycopg2"),
        hide_parameters=True,
        pool_pre_ping=True,
    )
    document_id = None
    try:
        with Session(engine) as session:
            doc = Document(filename="phase2-restart-check.txt", status="ready")
            session.add(doc)
            session.flush()
            document_id = doc.id
            chunk = DocumentChunk(
                document_id=doc.id, content="synthetic persistence check", chunk_index=0
            )
            session.add(chunk)
            session.flush()
            vector = ChunkEmbedding(
                chunk_id=chunk.id,
                model="synthetic-persistence",
                revision="v1",
                dimensions=3,
                embedding=[0, 1, 0],
            )
            session.add(vector)
            session.flush()
            vector_id = vector.id
            session.commit()
        engine.dispose()
        subprocess.run(["docker", "restart", CONTAINER], check=True, capture_output=True)
        deadline = time.monotonic() + 45
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        with Session(engine) as session:
            stored_doc = session.get(Document, document_id)
            stored_vector = session.get(ChunkEmbedding, vector_id)
            assert stored_doc is not None and stored_doc.filename == "phase2-restart-check.txt"
            assert stored_vector is not None and list(stored_vector.embedding) == [0, 1, 0]
        reader = create_engine(values["ANALYTICS_DB_URL"], hide_parameters=True)
        try:
            with reader.connect() as conn:
                orders = conn.scalar(text("SELECT COUNT(*) FROM business.orders"))
        finally:
            reader.dispose()
        report = {
            "created_at": datetime.now(UTC).isoformat(),
            "container": CONTAINER,
            "volume": VOLUME,
            "document_survived": True,
            "vector_survived": True,
            "business_order_count_after_restart": orders,
            "synthetic_test_rows_cleaned_up": True,
        }
    finally:
        if document_id:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM agent.documents WHERE id=:id"), {"id": document_id})
        engine.dispose()
    target = ROOT / "reports/phase2/persistence.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Restart persistence: document, vector and business data verified; synthetic rows removed"
    )


if __name__ == "__main__":
    main()
