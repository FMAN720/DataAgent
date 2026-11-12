"""Business document dates and types, independent of upload timestamps."""
from alembic import op
import sqlalchemy as sa

revision = "0004_document_metadata"
down_revision = "0003_dataset_imports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("document_date", sa.Date(), nullable=True), schema="agent")
    op.add_column("documents", sa.Column("document_type", sa.String(32), nullable=False, server_default="reference"), schema="agent")
    op.create_index("ix_documents_date_type", "documents", ["document_date", "document_type"], schema="agent")


def downgrade():
    op.drop_index("ix_documents_date_type", table_name="documents", schema="agent")
    op.drop_column("documents", "document_type", schema="agent")
    op.drop_column("documents", "document_date", schema="agent")
