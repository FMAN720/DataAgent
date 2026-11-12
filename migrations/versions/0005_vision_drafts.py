"""Local recognition drafts; unreviewed results never enter retrieval or SQL."""
from alembic import op
import sqlalchemy as sa

revision = "0005_vision_drafts"
down_revision = "0004_document_metadata"
branch_labels = depends_on = None


def upgrade():
    op.create_table("vision_drafts",
        sa.Column("id",sa.UUID(as_uuid=False),primary_key=True),
        sa.Column("filename",sa.String(255),nullable=False),
        sa.Column("mime_type",sa.String(64),nullable=False),
        sa.Column("storage_path",sa.Text(),nullable=False),
        sa.Column("content_hash",sa.String(64),nullable=False),
        sa.Column("page_count",sa.Integer(),nullable=False),
        sa.Column("status",sa.String(32),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),
        sa.Column("state",sa.JSON(),nullable=False),
        sa.Column("lease_token",sa.UUID(as_uuid=False)),
        sa.Column("locked_until",sa.DateTime(timezone=True)),
        sa.Column("error",sa.Text()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),schema="agent")


def downgrade():
    op.drop_table("vision_drafts",schema="agent")
