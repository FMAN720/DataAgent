"""Document identity, index state and durable index jobs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_document_indexing"
down_revision = "0001_phase2_storage"
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column("content_hash", sa.String(64)),
        sa.Column("index_status", sa.String(32), nullable=False, server_default="not_indexed"),
        sa.Column("index_error", sa.Text), sa.Column("index_model", sa.String(256)),
        sa.Column("index_revision", sa.String(128)), sa.Column("index_dimensions", sa.Integer),
    ]:
        op.add_column("documents", column, schema="agent")
    op.create_unique_constraint("uq_documents_content_hash", "documents", ["content_hash"], schema="agent")
    op.create_table("index_jobs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=False), sa.ForeignKey("agent.documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(256), nullable=False), sa.Column("revision", sa.String(128), nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False), sa.Column("completed_chunks", sa.Integer, nullable=False),
        sa.Column("total_chunks", sa.Integer, nullable=False), sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)), sa.Column("lease_token", UUID(as_uuid=False)),
        sa.Column("error", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_index_jobs_document"), schema="agent")
    op.create_index("ix_agent_index_jobs_available_at", "index_jobs", ["available_at"], schema="agent")
    op.create_index("ix_chunk_embeddings_profile", "chunk_embeddings", ["model", "revision", "dimensions"], schema="agent")


def downgrade():
    op.drop_index("ix_chunk_embeddings_profile", table_name="chunk_embeddings", schema="agent")
    op.drop_table("index_jobs", schema="agent")
    op.drop_constraint("uq_documents_content_hash", "documents", schema="agent")
    for name in ["index_dimensions", "index_revision", "index_model", "index_error", "index_status", "content_hash"]:
        op.drop_column("documents", name, schema="agent")
