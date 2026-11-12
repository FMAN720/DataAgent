"""Shared embedding and retrieval contracts, independent of PostgreSQL drivers."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel, Field

DocumentType = Literal["reference", "weekly_report", "review", "campaign", "support"]
DOCUMENT_TYPES = {"reference", "weekly_report", "review", "campaign", "support"}


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    revision: str  # fingerprint of provider URL + model + dimensions + revision
    dimensions: int

    @classmethod
    def create(cls, provider: str, model: str, dimensions: int, revision: str):
        value = json.dumps(
            [provider.rstrip("/"), model, dimensions, revision], separators=(",", ":")
        )
        return cls(model, hashlib.sha256(value.encode()).hexdigest(), dimensions)


class AsyncEmbedder(Protocol):
    profile: EmbeddingProfile

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class KnowledgeError(ValueError):
    """Safe error code/message; never contains provider response bodies or keys."""


class EmbeddingError(KnowledgeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.retryable = retryable


class SearchHit(BaseModel):
    reference: str
    document_id: str
    chunk_id: str
    chunk_index: int
    filename: str
    content: str
    score: float
    source: str
    document_date: date | None = None
    document_type: str = "reference"
    page_number: int | None = None
    section: str | None = None


class SearchResponse(BaseModel):
    mode: str
    hits: list[SearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0


class DocumentCitation(BaseModel):
    reference: str
    document_id: str
    chunk_id: str
    chunk_index: int
    filename: str
    snippet: str
    document_date: date | None = None
    document_type: str = "reference"
    page_number: int | None = None
    section: str | None = None
