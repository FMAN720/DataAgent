"""Storage compatibility checks that also run without PostgreSQL dependencies."""

import sqlite3

from app.analytics.engine import AnalyticsDatabase
from app.config import Settings
from app.infrastructure.database.manage import DatabaseConfig
from eval.run_eval import gold_rows
from tests.conftest import run


def test_sqlite_identifier_quoting_and_schema_refresh(tmp_path):
    path = tmp_path / "quoted.db"
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE "a""b"(v INTEGER)')
        conn.execute('INSERT INTO "a""b" VALUES (7)')
    db = AnalyticsDatabase("sqlite:///" + path.as_posix())
    try:
        assert run(db.sample_rows('a"b')).rows == [[7]]
        assert run(db.list_tables())[0].qualified_name == 'a"b'
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE added(v INTEGER)")
        assert len(run(db.list_tables())) == 1
        assert len(run(db.refresh_schema())) == 2
    finally:
        db.dispose()


def test_gold_sql_preserves_fraction_before_rounding(demo_db_path):
    assert gold_rows("sqlite:///" + demo_db_path.as_posix(), "SELECT 1009 * 1.0 / 2") == [(504.5,)]


def test_credentials_are_excluded_from_debug_representations():
    settings = Settings(
        _env_file=None,
        openai_api_key="secret-key",
        database_url="secret-db",
        analytics_db_url="secret-analytics",
    )
    assert "secret-" not in repr(settings)
    config = DatabaseConfig(password="secret-password")
    assert "secret-password" not in repr(config) and "secret-password" not in str(config)
