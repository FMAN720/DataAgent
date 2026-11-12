"""Use a dedicated migration URL; never run DDL with the application's role."""
from alembic import context
from sqlalchemy import create_engine

from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import Base
from app.infrastructure.database import pg_models  # noqa: F401

config = context.config


def run_migrations():
    url = config.attributes.get("database_url") or load_config()["MIGRATION_DB_URL"]
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata,
                              include_schemas=True, version_table_schema="agent")
            with context.begin_transaction():
                context.run_migrations()
                connection.exec_driver_sql("REVOKE ALL ON agent.alembic_version FROM analyst_app")
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Use online migrations against the dedicated phase 2 database")
run_migrations()
