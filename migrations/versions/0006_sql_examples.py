"""User-confirmed question/SQL pairs, recalled as few-shot references."""

import sqlalchemy as sa
from alembic import op

revision = "0006_sql_examples"
down_revision = "0005_vision_drafts"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "sql_examples",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_report_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_report_id", name="uq_sql_examples_report"),
        schema="agent",
    )


def downgrade():
    op.drop_table("sql_examples", schema="agent")
