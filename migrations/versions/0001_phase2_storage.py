"""Initial business and agent storage, frozen independently of live ORM models."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

revision = "0001_phase2_storage"
down_revision = None
branch_labels = None
depends_on = None

# name, columns(name, type, nullable, FK target, comment). Money stays in integer cents;
# ISO date strings intentionally preserve the demo's LIKE/substr query contract.
BUSINESS = [
    ("regions", [("region_code", "text", False, None, "区域编码 EAST/NORTH/SOUTH/CENTRAL/WEST"), ("region_name", "text", False, None, "华东/华北/华南/华中/西部")]),
    ("categories", [("category_id", "int", False, None, ""), ("category_name", "text", False, None, "一级品类"), ("parent_id", "int", True, None, "预留，当前均为 NULL")]),
    ("products", [("product_id", "int", False, None, ""), ("product_name", "text", False, None, ""), ("category_id", "int", False, "categories.category_id", ""), ("list_price", "money", False, None, "标价，单位分"), ("cost_price", "money", False, None, "成本，单位分"), ("is_active", "int", False, None, "1 在售，0 下架")]),
    ("users", [("user_id", "int", False, None, ""), ("register_date", "text", False, None, "YYYY-MM-DD"), ("region_code", "text", False, "regions.region_code", ""), ("gender", "text", True, None, "M/F/NULL"), ("birth_year", "int", True, None, ""), ("member_level", "text", False, None, "normal/silver/gold"), ("acquire_channel", "text", False, None, "organic/ads/referral"), ("is_test", "int", False, None, "1 测试账号，统计必须排除")]),
    ("orders", [("order_id", "int", False, None, ""), ("user_id", "int", False, "users.user_id", ""), ("order_time", "text", False, None, "YYYY-MM-DD HH:MM:SS"), ("status", "text", False, None, "paid 已支付/completed 已完成/cancelled 未支付/refunded 全额退款"), ("channel", "text", False, None, "app/web/mini_program"), ("total_amount", "money", False, None, "原价，单位分"), ("discount_amount", "money", False, None, "优惠，单位分"), ("pay_amount", "money", False, None, "实付=原价-优惠，单位分；cancelled 为0"), ("pay_time", "text", True, None, "cancelled 为 NULL")]),
    ("order_items", [("item_id", "int", False, None, ""), ("order_id", "int", False, "orders.order_id", ""), ("product_id", "int", False, "products.product_id", ""), ("quantity", "int", False, None, ""), ("unit_price", "money", False, None, "未分摊优惠，单位分"), ("item_amount", "money", False, None, "quantity*unit_price，单位分")]),
    ("payments", [("payment_id", "int", False, None, ""), ("order_id", "int", False, "orders.order_id", ""), ("pay_method", "text", False, None, "alipay/wechat/card"), ("amount", "money", False, None, "单位分"), ("paid_at", "text", False, None, "YYYY-MM-DD HH:MM:SS")]),
    ("refunds", [("refund_id", "int", False, None, ""), ("order_id", "int", False, "orders.order_id", ""), ("refund_amount", "money", False, None, "实际退款，单位分"), ("refund_time", "text", False, None, "YYYY-MM-DD HH:MM:SS"), ("reason", "text", False, None, "quality/not_needed/late_delivery/other")]),
    ("refund_requests", [("request_id", "int", False, None, ""), ("order_id", "int", False, "orders.order_id", ""), ("request_time", "text", False, None, "YYYY-MM-DD HH:MM:SS"), ("status", "text", False, None, "approved 通过会产生 refunds/rejected 驳回"), ("reason", "text", False, None, "")]),
    ("user_events", [("event_id", "int", False, None, ""), ("user_id", "int", False, "users.user_id", ""), ("event_date", "text", False, None, "YYYY-MM-DD"), ("event_type", "text", False, None, "login/view/add_cart")]),
]


def _id():
    return sa.Column("id", UUID(as_uuid=False), primary_key=True)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade():
    for name, specs in BUSINESS:
        columns = []
        for i, (col, kind, nullable, target, comment) in enumerate(specs):
            typ = {"text": sa.Text, "int": sa.Integer, "money": sa.BigInteger}[kind]()
            args = [sa.ForeignKey("business." + target)] if target else []
            default = "1" if (name, col) == ("products", "is_active") else "0" if (name, col) == ("users", "is_test") else None
            columns.append(sa.Column(col, typ, *args, nullable=nullable, primary_key=i == 0,
                                     autoincrement=False, comment=comment or None, server_default=default))
        op.create_table(name, *columns, schema="business", comment="电商演示数据；金额为分，日期为 ISO 文本")
    for name, table, cols in [("idx_orders_time", "orders", ["order_time"]), ("idx_orders_user", "orders", ["user_id"]), ("idx_items_order", "order_items", ["order_id"]), ("idx_events_date", "user_events", ["event_date"])]:
        op.create_index(name, table, cols, schema="business")

    op.create_table("conversations", _id(), sa.Column("title", sa.String(512)), sa.Column("user_id", sa.String(128)), sa.Column("meta", sa.JSON), _created(), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), schema="agent")
    op.create_table("messages", _id(), sa.Column("conversation_id", UUID(as_uuid=False), sa.ForeignKey("agent.conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("content", sa.Text, nullable=False), sa.Column("token_count", sa.Integer), sa.Column("meta", sa.JSON), _created(), schema="agent")
    op.create_table("documents", _id(), sa.Column("filename", sa.String(1024), nullable=False), sa.Column("mime_type", sa.String(256)), sa.Column("storage_path", sa.String(2048)), sa.Column("status", sa.String(64), nullable=False), sa.Column("meta", sa.JSON), _created(), schema="agent")
    op.create_table("document_chunks", _id(), sa.Column("document_id", UUID(as_uuid=False), sa.ForeignKey("agent.documents.id", ondelete="CASCADE"), nullable=False), sa.Column("chunk_index", sa.Integer, nullable=False), sa.Column("content", sa.Text, nullable=False), sa.Column("vector_id", sa.String(128)), sa.Column("meta", sa.JSON), _created(), schema="agent")
    op.create_table("trace_logs", _id(), sa.Column("trace_id", sa.String(64), nullable=False), sa.Column("span_id", sa.String(64), nullable=False), sa.Column("operation", sa.String(256), nullable=False), sa.Column("payload", sa.JSON), sa.Column("error", sa.Text), sa.Column("duration_ms", sa.Integer), _created(), schema="agent")
    op.create_table("chunk_embeddings", _id(), sa.Column("chunk_id", UUID(as_uuid=False), sa.ForeignKey("agent.document_chunks.id", ondelete="CASCADE"), nullable=False), sa.Column("model", sa.String(256), nullable=False), sa.Column("revision", sa.String(128), nullable=False), sa.Column("dimensions", sa.Integer, nullable=False), sa.Column("embedding", Vector(), nullable=False), _created(), sa.UniqueConstraint("chunk_id", "model", "revision", name="uq_chunk_embedding_version"), sa.CheckConstraint("dimensions > 0 AND vector_dims(embedding) = dimensions", name="ck_embedding_dimensions"), schema="agent")
    for table, col in [("conversations", "user_id"), ("messages", "conversation_id"), ("document_chunks", "document_id"), ("trace_logs", "trace_id"), ("trace_logs", "span_id"), ("chunk_embeddings", "chunk_id")]:
        op.create_index(f"ix_agent_{table}_{col}", table, [col], schema="agent")


def downgrade():
    for name in ["chunk_embeddings", "trace_logs", "document_chunks", "documents", "messages", "conversations"]:
        op.drop_table(name, schema="agent")
    for name, _ in reversed(BUSINESS):
        op.drop_table(name, schema="business")
