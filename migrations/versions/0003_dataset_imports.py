"""Durable reviewed dataset imports; data tables are created only by importer role."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_dataset_imports"
down_revision = "0002_document_indexing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("uploaded_datasets",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("filename", sa.String(1024), nullable=False), sa.Column("storage_path", sa.String(2048), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("table_name", sa.String(64), nullable=False), sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("options", sa.JSON, nullable=False), sa.Column("columns", sa.JSON, nullable=False),
        sa.Column("preview_rows", sa.JSON, nullable=False), sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False), sa.Column("imported_rows", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False), sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", UUID(as_uuid=False)), sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_uploaded_datasets_fingerprint"),
        sa.UniqueConstraint("table_name", name="uq_uploaded_datasets_table_name"), schema="agent")
    op.create_index("ix_agent_uploaded_datasets_status", "uploaded_datasets", ["status"], schema="agent")


def downgrade():
    # uploads data tables deliberately remain; reverting metadata never drops user data.
    op.drop_table("uploaded_datasets", schema="agent")
