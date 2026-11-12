"""Explicit local database provisioning; never called during API startup.

init -> compose up -> bootstrap -> upgrade -> import-demo. No package/image downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / ".env.phase2"
DATABASE = "analyst_phase2"
ROLES = {
    "MIGRATION_DB_URL": "analyst_migrator",
    "DATABASE_URL": "analyst_app",
    "IMPORT_DB_URL": "analyst_importer",
    "ANALYTICS_DB_URL": "analyst_reader",
}
TABLES = (
    "regions",
    "categories",
    "products",
    "users",
    "orders",
    "order_items",
    "payments",
    "refunds",
    "refund_requests",
    "user_events",
)


class DatabaseConfig(dict):
    """Never put credentials in test failure locals or debug representations."""

    def __repr__(self):
        return "DatabaseConfig(credentials hidden)"

    __str__ = __repr__


def init_config() -> None:
    """Create once, never regenerate credentials for an existing database volume."""
    values = {"PHASE2_POSTGRES_PASSWORD": secrets.token_hex(24)}
    for key, role in ROLES.items():
        driver = "postgresql+asyncpg" if key == "DATABASE_URL" else "postgresql+psycopg2"
        values[key] = URL.create(
            driver,
            username=role,
            password=secrets.token_hex(24),
            host="127.0.0.1",
            port=15432,
            database=DATABASE,
        ).render_as_string(hide_password=False)
    values["ANALYTICS_DB_SCHEMAS"] = '\'["business","uploads"]\''
    values["DOCUMENTS_ENABLED"] = "true"
    with CONFIG_PATH.open("x", encoding="utf-8") as output:
        output.write("# Local credentials, generated once. Excluded from Git.\n")
        output.writelines(f"{key}={value}\n" for key, value in values.items())


def load_config() -> dict[str, str]:
    values = DatabaseConfig({k: v for k, v in dotenv_values(CONFIG_PATH).items() if v is not None})
    if not values.get("PHASE2_POSTGRES_PASSWORD"):
        raise ValueError("Run database.manage init first")
    # These commands deliberately target only the dedicated local database.
    for key, role in ROLES.items():
        url = make_url(values[key])
        if (url.host, url.port, url.database, url.username) != ("127.0.0.1", 15432, DATABASE, role):
            raise ValueError(
                f"Unexpected target in {key}; provisioning is restricted to the project database"
            )
    return values


def rotate_passwords(values: dict[str, str]) -> None:
    """Rotate only this project's five roles and atomically replace local config."""
    import psycopg2
    from psycopg2 import sql

    updated = DatabaseConfig(values)
    updated["PHASE2_POSTGRES_PASSWORD"] = secrets.token_hex(24)
    for key in ROLES:
        updated[key] = (
            make_url(values[key])
            .set(password=secrets.token_hex(24))
            .render_as_string(hide_password=False)
        )
    temporary = CONFIG_PATH.with_suffix(".phase2.next")
    with temporary.open("x", encoding="utf-8") as output:
        output.write("# Local credentials. Excluded from Git.\n")
        output.writelines(f"{key}={value}\n" for key, value in updated.items())
    try:
        with closing(
            psycopg2.connect(
                host="127.0.0.1",
                port=15432,
                dbname=DATABASE,
                user="postgres",
                password=values["PHASE2_POSTGRES_PASSWORD"],
            )
        ) as conn:
            with conn, conn.cursor() as cur:
                for key, role in ROLES.items():
                    cur.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(make_url(updated[key]).password)
                        )
                    )
                cur.execute(
                    sql.SQL("ALTER ROLE postgres PASSWORD {}").format(
                        sql.Literal(updated["PHASE2_POSTGRES_PASSWORD"])
                    )
                )
        temporary.replace(CONFIG_PATH)
    except BaseException:
        # Retain .next if the database committed but replacing the file failed,
        # so the new credentials remain recoverable locally.
        raise


def bootstrap(values: dict[str, str]) -> None:
    """Roles, schemas, extension and future-object grants, using the local admin only."""
    import psycopg2
    from psycopg2 import sql

    with closing(
        psycopg2.connect(
            host="127.0.0.1",
            port=15432,
            dbname=DATABASE,
            user="postgres",
            password=values["PHASE2_POSTGRES_PASSWORD"],
        )
    ) as conn:
        with conn, conn.cursor() as cur:
            for key, role in ROLES.items():
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if not cur.fetchone():
                    cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
                cur.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(role), sql.Literal(make_url(values[key]).password))
                )
                cur.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(DATABASE), sql.Identifier(role)
                    )
                )
            cur.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE))
            )
            cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
            for schema, owner in [
                ("business", "analyst_migrator"),
                ("agent", "analyst_migrator"),
                ("uploads", "analyst_importer"),
            ]:
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                        sql.Identifier(schema), sql.Identifier(owner)
                    )
                )
                cur.execute(
                    sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema))
                )
            cur.execute("GRANT USAGE ON SCHEMA public TO analyst_migrator, analyst_app")
            cur.execute("GRANT USAGE ON SCHEMA business, uploads TO analyst_reader")
            cur.execute("GRANT USAGE ON SCHEMA agent TO analyst_app")
            cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA business, uploads TO analyst_reader")
            cur.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent TO analyst_app"
            )
            cur.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE analyst_migrator IN SCHEMA business GRANT SELECT ON TABLES TO analyst_reader"
            )
            cur.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE analyst_importer IN SCHEMA uploads GRANT SELECT ON TABLES TO analyst_reader"
            )
            cur.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE analyst_migrator IN SCHEMA agent GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO analyst_app"
            )
            cur.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE analyst_migrator IN SCHEMA agent GRANT USAGE, SELECT ON SEQUENCES TO analyst_app"
            )
            paths = {
                "analyst_migrator": "pg_catalog, agent, business, public",
                "analyst_app": "pg_catalog, agent, public",
                "analyst_importer": "pg_catalog, uploads",
                "analyst_reader": "pg_catalog, business, uploads",
            }
            for role, path in paths.items():
                cur.execute(
                    sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path TO " + path).format(
                        sql.Identifier(role), sql.Identifier(DATABASE)
                    )
                )
            cur.execute(
                sql.SQL(
                    "ALTER ROLE analyst_reader IN DATABASE {} SET default_transaction_read_only TO on"
                ).format(sql.Identifier(DATABASE))
            )
            # The Alembic ledger is never application data (also on re-bootstrap).
            cur.execute("SELECT to_regclass('agent.alembic_version')")
            if cur.fetchone()[0]:
                cur.execute("REVOKE ALL ON agent.alembic_version FROM analyst_app")


def upgrade(values: dict[str, str]) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = values["MIGRATION_DB_URL"]
    command.upgrade(cfg, "head")
    engine = create_engine(values["MIGRATION_DB_URL"], hide_parameters=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("REVOKE ALL ON agent.alembic_version FROM analyst_app"))
    finally:
        engine.dispose()


def row_digest(rows) -> dict:
    digest, count = hashlib.sha256(), 0
    for row in rows:
        digest.update(
            json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def import_demo(values: dict[str, str], source: Path) -> dict:
    """Single transaction, exact row checksums, no overwrite of nonmatching data."""
    if not source.is_file():
        raise ValueError("SQLite source does not exist")
    uri = "file:" + quote(source.resolve().as_posix(), safe="/:") + "?mode=ro"
    engine = create_engine(values["MIGRATION_DB_URL"], hide_parameters=True)
    try:
        with closing(sqlite3.connect(uri, uri=True)) as sqlite, engine.begin() as conn:
            sqlite.execute("BEGIN")  # stable source snapshot through validation
            conn.execute(text("SELECT pg_advisory_xact_lock(20251231)"))
            conn.execute(
                text(
                    "LOCK TABLE "
                    + ", ".join("business." + n for n in TABLES)
                    + " IN SHARE ROW EXCLUSIVE MODE"
                )
            )
            metadata = MetaData()
            tables = {
                name: Table(name, metadata, schema="business", autoload_with=conn)
                for name in TABLES
            }
            originals, current = {}, {}
            queries = {}
            for name, table in tables.items():
                cols = ", ".join('"' + c.name + '"' for c in table.columns)
                pk = next(iter(table.primary_key.columns)).name
                queries[name] = f'SELECT {cols} FROM "{name}" ORDER BY "{pk}"'
                originals[name] = row_digest(sqlite.execute(queries[name]))
                current[name] = row_digest(
                    conn.execute(select(table).order_by(*table.primary_key.columns))
                )
            if any(v["rows"] for v in current.values()):
                if current != originals:
                    raise ValueError(
                        "Target contains different data; import aborted without changes"
                    )
                return {"status": "already_identical", "tables": originals}
            for name, table in tables.items():
                cursor = sqlite.execute(queries[name])
                while batch := cursor.fetchmany(1000):
                    conn.execute(
                        table.insert(), [dict(zip(table.columns.keys(), row)) for row in batch]
                    )
                actual = row_digest(
                    conn.execute(select(table).order_by(*table.primary_key.columns))
                )
                if actual != originals[name]:
                    raise ValueError(f"Checksum mismatch in {name}; entire import rolled back")
            return {"status": "imported", "tables": originals}
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["init", "bootstrap", "upgrade", "import-demo", "rotate-passwords"]
    )
    parser.add_argument("--source", type=Path, default=ROOT / "data/ecommerce.db")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        init_config()
        print("Created .env.phase2 (credentials hidden)")
        return
    values = load_config()
    if args.command == "bootstrap":
        bootstrap(values)
    elif args.command == "upgrade":
        upgrade(values)
    elif args.command == "rotate-passwords":
        rotate_passwords(values)
    else:
        result = import_demo(values, args.source)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{result['status']}: {len(result['tables'])} tables verified")
        return
    print(f"{args.command}: complete")


if __name__ == "__main__":
    main()
