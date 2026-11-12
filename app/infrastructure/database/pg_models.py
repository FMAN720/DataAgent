"""PostgreSQL 专用向量表；仅 PG 工具导入，SQLite 路径不加载可选依赖。"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models import Base, _utcnow, _uuid


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model", "revision", name="uq_chunk_embedding_version"),
        CheckConstraint(
            "dimensions > 0 AND vector_dims(embedding) = dimensions", name="ck_embedding_dimensions"
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    chunk_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("agent.document_chunks.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(256))
    revision: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
