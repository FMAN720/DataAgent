"""Real phase 2 database acceptance checks; opt in explicitly, no API calls."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.analytics.engine import AnalyticsDatabase, SQLValidationError
from app.analytics.tools import DescribeTablesTool, ListTablesTool
from app.infrastructure.database.manage import ROOT, import_demo, load_config
from eval.phase2_parity import equivalent
from eval.run_eval import gold_rows
from tests.conftest import run

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1",
        reason="Set RUN_POSTGRES_TESTS=1 for the dedicated local PostgreSQL",
    ),
]


@pytest.fixture(scope="module")
def values():
    return load_config()


@contextmanager
def connection(values, key):
    url = make_url(values[key]).set(drivername="postgresql+psycopg2")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


@pytest.fixture
def db(values):
    database = AnalyticsDatabase(values["ANALYTICS_DB_URL"], timeout_seconds=2)
    try:
        yield database
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "key,statement",
    [
        ("ANALYTICS_DB_URL", "DELETE FROM business.orders WHERE FALSE"),
        ("ANALYTICS_DB_URL", "CREATE TABLE uploads.forbidden_reader_table(id int)"),
        ("ANALYTICS_DB_URL", "SELECT * FROM agent.documents"),
        ("ANALYTICS_DB_URL", "CREATE TEMP TABLE forbidden_temp(id int)"),
        ("ANALYTICS_DB_URL", "SET ROLE analyst_migrator"),
        ("DATABASE_URL", "DELETE FROM business.orders WHERE FALSE"),
        ("DATABASE_URL", "CREATE TABLE agent.forbidden_app_table(id int)"),
        ("DATABASE_URL", "CREATE TABLE uploads.forbidden_app_table(id int)"),
        ("DATABASE_URL", "UPDATE agent.alembic_version SET version_num = version_num"),
        ("DATABASE_URL", "CREATE TEMP TABLE forbidden_temp(id int)"),
        ("IMPORT_DB_URL", "DELETE FROM business.orders WHERE FALSE"),
        ("IMPORT_DB_URL", "SELECT * FROM agent.documents"),
        ("IMPORT_DB_URL", "CREATE TABLE business.forbidden_import_table(id int)"),
        ("IMPORT_DB_URL", "CREATE TABLE public.forbidden_import_table(id int)"),
        ("MIGRATION_DB_URL", "CREATE TABLE public.forbidden_migration_table(id int)"),
    ],
)
def test_permissions_even_without_application_validator(values, key, statement):
    with connection(values, key) as conn:
        # Turn off the role's default read-only mode: privileges must STILL deny writes.
        conn.exec_driver_sql("SET default_transaction_read_only = off")
        conn.commit()
        with pytest.raises(DBAPIError) as error:
            conn.exec_driver_sql(statement)
        assert error.value.orig.pgcode == "42501"
        conn.rollback()


def test_analytics_transaction_readonly_even_with_migration_role(values):
    privileged = AnalyticsDatabase(values["MIGRATION_DB_URL"])
    try:
        # Direct engine access bypasses validate_readonly_sql.
        with privileged._engine.connect() as conn:
            with pytest.raises(DBAPIError) as error:
                conn.exec_driver_sql("DELETE FROM business.orders WHERE FALSE")
            assert error.value.orig.pgcode == "25006"
    finally:
        privileged.dispose()


def test_importer_cross_schema_join_schema_refresh_and_quoting(values, db):
    # business 有 10 张演示表；uploads 里可能已有用户导入的表
    baseline = len(run(db.list_tables()))
    assert baseline >= 10
    suffix = uuid.uuid4().hex[:8]
    name = 'phase2_quoted"' + suffix
    quoted = '"' + name.replace('"', '""') + '"'
    with connection(values, "IMPORT_DB_URL") as conn:
        duplicate_created = False
        try:
            conn.exec_driver_sql(
                f"CREATE TABLE uploads.{quoted}(region_code text PRIMARY KEY, target_cents bigint)"
            )
            conn.exec_driver_sql(f"INSERT INTO uploads.{quoted} VALUES ('EAST', 12345)")
            conn.commit()
            assert len(run(db.list_tables())) == baseline  # cached
            refreshed = run(db.refresh_schema())
            assert any(t.qualified_name == f"uploads.{name}" for t in refreshed)
            assert run(db.sample_rows(f"uploads.{name}")).rows == [["EAST", 12345]]
            result = run(
                db.execute(
                    f"SELECT r.region_name, u.target_cents FROM business.regions r JOIN uploads.{quoted} u USING(region_code)"
                )
            )
            assert result.rows == [["华东", 12345]]
            listed = run(ListTablesTool(db).execute())
            assert all(not t["table"].startswith("agent.") for t in listed["tables"])
            described = run(DescribeTablesTool(db).execute(tables=["business.orders"]))
            assert "单位分" in described and "business.users" in described
            # A real duplicate name must require the schema, even though search_path has a default.
            conn.exec_driver_sql("CREATE TABLE uploads.regions(region_code text)")
            conn.commit()
            duplicate_created = True
            run(db.refresh_schema())
            with pytest.raises(SQLValidationError, match="歧义"):
                run(db.describe(["regions"]))
            assert run(db.describe(["business.regions"]))[0][0].schema == "business"
        finally:
            conn.rollback()
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS uploads.{quoted}")
            if duplicate_created:
                conn.exec_driver_sql("DROP TABLE uploads.regions")
            conn.commit()


def test_schema_cache_ttl_zero(values):
    database = AnalyticsDatabase(values["ANALYTICS_DB_URL"], schema_cache_ttl_seconds=0)
    try:
        run(database.list_tables())
        first = database._schema_cache
        run(database.list_tables())
        assert database._schema_cache is not first
    finally:
        database.dispose()


def test_timeout_rolls_back_and_connection_recovers(values):
    database = AnalyticsDatabase(values["ANALYTICS_DB_URL"], timeout_seconds=0.1)
    try:
        with pytest.raises(SQLValidationError):
            run(database.execute("SELECT pg_sleep(2)"))
        assert run(database.execute("SELECT 42")).rows == [[42]]
    finally:
        database.dispose()


def test_vector_roundtrip_constraints_and_transaction_rollback(values):
    from app.infrastructure.database.models import Document, DocumentChunk
    from app.infrastructure.database.pg_models import ChunkEmbedding

    with connection(values, "DATABASE_URL") as conn:
        with Session(bind=conn) as session:
            doc = Document(filename="phase2-integration.txt", status="ready")
            session.add(doc)
            session.flush()
            chunk = DocumentChunk(
                document_id=doc.id, content="Synthetic integration text", chunk_index=0
            )
            session.add(chunk)
            session.flush()
            embedding = ChunkEmbedding(
                chunk_id=chunk.id,
                model="synthetic-test",
                revision="v1",
                dimensions=3,
                embedding=[1, 0, 0],
            )
            session.add(embedding)
            session.flush()
            session.expire_all()
            stored = session.scalar(select(ChunkEmbedding).where(ChunkEmbedding.id == embedding.id))
            assert list(stored.embedding) == [1, 0, 0]
            assert stored.dimensions == 3 and stored.model == "synthetic-test"
            assert (
                session.scalar(
                    text(
                        "SELECT embedding <=> '[1,0,0]'::public.vector FROM agent.chunk_embeddings WHERE id=:id"
                    ),
                    {"id": embedding.id},
                )
                == 0
            )
            # Each vector's dimension is enforced by the database.
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.add(
                        ChunkEmbedding(
                            chunk_id=chunk.id,
                            model="synthetic-test",
                            revision="bad-dim",
                            dimensions=4,
                            embedding=[1, 0, 0],
                        )
                    )
                    session.flush()
            document_id = doc.id
            session.rollback()
            assert session.scalar(select(Document).where(Document.id == document_id)) is None


def test_import_is_idempotent_and_refuses_different_data(values, tmp_path):
    assert import_demo(values, ROOT / "data/ecommerce.db")["status"] == "already_identical"
    import shutil
    import sqlite3

    source = tmp_path / "different.db"
    shutil.copyfile(ROOT / "data/ecommerce.db", source)
    with sqlite3.connect(source) as sqlite:
        sqlite.execute("UPDATE regions SET region_name = 'different' WHERE region_code='EAST'")
    with pytest.raises(ValueError, match="different data"):
        import_demo(values, source)
    assert gold_rows(
        values["ANALYTICS_DB_URL"], "SELECT region_name FROM regions WHERE region_code='EAST'"
    ) == [("华东",)]


CASES = json.loads((ROOT / "eval/questions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_all_twelve_gold_queries_match_sqlite(values, case):
    sqlite = gold_rows("sqlite:///" + (ROOT / "data/ecommerce.db").as_posix(), case["gold_sql"])
    pg = gold_rows(values["ANALYTICS_DB_URL"], case["gold_sql"])
    assert len(sqlite) == len(pg)
    for left, right in zip(sqlite, pg):
        assert len(left) == len(right)
        assert all(equivalent(a, b) for a, b in zip(left, right))
    if case["id"] == "dec_aov":
        # Avoid integer division before converting cents to yuan.
        assert Decimal(str(pg[0][0])) == Decimal("1002.70")


def test_real_api_readiness_upload_and_list_without_llm(values, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main
    from app.config import Settings, get_settings

    settings = Settings(
        _env_file=None,
        openai_api_key="offline-test",
        documents_enabled=True,
        analytics_db_url=values["ANALYTICS_DB_URL"],
        database_url=values["DATABASE_URL"],
        upload_dir=tmp_path / "uploads",
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    application = main.create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    document_id = None
    try:
        with TestClient(application) as client:
            readiness = client.get("/api/v1/health/ready")
            assert readiness.status_code == 200 and readiness.json()["status"] == "ready"
            response = client.post(
                "/api/v1/documents/upload",
                files={
                    "file": ("phase2-api.txt", "这是本地数据库集成测试。".encode(), "text/plain")
                },
            )
            assert response.status_code == 200, response.text
            document_id = response.json()["document_id"]
            assert response.json()["chunk_count"] > 0
            assert any(d["id"] == document_id for d in client.get("/api/v1/documents").json())
    finally:
        if document_id:
            with connection(values, "DATABASE_URL") as conn:
                conn.execute(text("DELETE FROM agent.documents WHERE id=:id"), {"id": document_id})
                conn.commit()
