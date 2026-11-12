"""
业务口径知识库：把 Markdown 口径文档按 ``##`` 小节切块，用 BM25 检索。

口径文档条目少、术语明确，关键词检索足够，不需要 embedding 模型或向量库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.core.rag.retriever import MultiRetriever


@dataclass
class GlossaryEntry:
    id: str
    title: str
    content: str


def split_markdown_sections(markdown: str) -> list[GlossaryEntry]:
    """按二级标题切块；标题本身也写进正文，便于按术语名命中。"""
    entries: list[GlossaryEntry] = []
    parts = re.split(r"^##\s+", markdown, flags=re.M)
    for idx, part in enumerate(parts[1:], start=1):
        title, _, body = part.partition("\n")
        title = title.strip()
        body = body.strip()
        if not body:
            continue
        entries.append(
            GlossaryEntry(id=f"glossary_{idx}", title=title, content=f"## {title}\n{body}")
        )
    return entries


class GlossaryStore:
    """口径检索。"""

    def __init__(self, entries: list[GlossaryEntry]) -> None:
        self._entries = {e.id: e for e in entries}
        self._retriever = MultiRetriever()
        self._retriever.register_keyword_documents({e.id: e.content for e in entries})
        logger.info("口径库已加载 {} 条", len(entries))

    @classmethod
    def from_file(cls, path: str | Path) -> GlossaryStore:
        text = Path(path).read_text(encoding="utf-8")
        return cls(split_markdown_sections(text))

    @property
    def titles(self) -> list[str]:
        return [e.title for e in self._entries.values()]

    async def search(self, query: str, top_k: int = 3) -> list[GlossaryEntry]:
        results = await self._retriever.keyword_search(query, top_k)
        return [self._entries[r.id] for r in results if r.id in self._entries]
