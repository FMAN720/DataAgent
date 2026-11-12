"""ETL 流水线：解析 -> 分块 ->（可选）向量写入回调。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.etl.chunker import ChunkStrategy, DocumentChunker
from app.etl.parser import DocumentParser, ParsedDocument


@dataclass
class ETLResult:
    """流水线输出。"""

    chunks: list[str]
    parsed: ParsedDocument
    meta: dict[str, Any]
    chunk_metadata: list[dict[str, Any]] = field(default_factory=list)


class ETLPipeline:
    """ETL 流水线：组合解析器与分块器。"""

    def __init__(
        self,
        parser: DocumentParser | None = None,
        chunker: DocumentChunker | None = None,
    ) -> None:
        self._parser = parser or DocumentParser()
        self._chunker = chunker or DocumentChunker()

    async def run_bytes(
        self,
        data: bytes,
        filename: str,
        mime_type: str | None,
        *,
        strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
        on_chunks: Callable[[list[str], ParsedDocument], Awaitable[None]] | None = None,
    ) -> ETLResult:
        """对上传字节执行解析与分块；可选异步回调处理向量入库等。"""
        try:
            parsed = await asyncio.to_thread(self._parser.parse_bytes, data, filename, mime_type)

            def split():
                chunks, metadata = [], []
                for part in parsed.segments:
                    values = self._chunker.chunk(part.text, strategy=strategy)
                    chunks.extend(values)
                    metadata.extend(
                        {"page_number": part.page_number, "section": part.section} for _ in values
                    )
                if not parsed.segments:
                    chunks = self._chunker.chunk(parsed.text, strategy=strategy)
                    metadata = [{} for _ in chunks]
                return chunks, metadata

            chunks, metadata = await asyncio.to_thread(split)
            result = ETLResult(
                chunks=chunks,
                parsed=parsed,
                meta={"chunk_count": len(chunks)},
                chunk_metadata=metadata,
            )
            if on_chunks is not None:
                await on_chunks(chunks, parsed)
            logger.info(
                "ETL 完成 file={} chunks={} mime={}",
                filename,
                len(chunks),
                parsed.mime_type,
            )
            return result
        except Exception as exc:
            logger.exception("ETL 流水线失败: {}", exc)
            raise
