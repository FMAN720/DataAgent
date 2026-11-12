"""文档解析器：按 MIME 类型选择解析策略。"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from loguru import logger


@dataclass
class ParsedSegment:
    text: str
    page_number: int | None = None
    section: str | None = None


@dataclass
class ParsedDocument:
    """解析后的纯文本与元数据。"""

    text: str
    mime_type: str
    meta: dict[str, Any]
    segments: list[ParsedSegment] = field(default_factory=list)


class DocumentParser:
    """Text/Markdown, text PDFs (optional pypdf), and bounded standard-library DOCX."""

    def __init__(self, max_chars: int = 500_000) -> None:
        self._max_chars = max_chars

    def parse_file(self, path: Path, mime_type: str | None = None) -> ParsedDocument:
        """从文件路径解析。"""
        return self.parse_bytes(path.read_bytes(), path.name, mime_type)

    def parse_bytes(self, data: bytes, filename: str, mime_type: str | None) -> ParsedDocument:
        """从内存字节解析。"""
        mime = mime_type or "application/octet-stream"
        lower = filename.lower()
        if lower.endswith((".txt", ".md")) or (mime.startswith("text/") and mime != "text/html"):
            text = data.decode("utf-8-sig")
            if len(text) > self._max_chars:
                raise ValueError("文档文字超过 50 万字符限制，请拆分文件")
            segments: list[ParsedSegment] = []
            section: str | None = None
            buffer: list[str] = []
            for line in text.splitlines():
                heading = re.match(r"^#{1,6}\s+(.+)$", line) if lower.endswith(".md") else None
                if heading:
                    if buffer:
                        segments.append(ParsedSegment("\n".join(buffer), section=section))
                    section, buffer = heading[1][:255], [line]
                else:
                    buffer.append(line)
            if buffer:
                segments.append(ParsedSegment("\n".join(buffer), section=section))
            return ParsedDocument(text=text, mime_type=mime, meta={}, segments=segments)

        if lower.endswith(".pdf") or mime == "application/pdf":
            return self._parse_pdf(data, mime)

        if lower.endswith(".docx"):
            return self._parse_docx(data, mime)

        return ParsedDocument(text="", mime_type=mime, meta={"warning": "unsupported"})

    def _parse_pdf(self, data: bytes, mime: str) -> ParsedDocument:
        """使用 pypdf 抽取文本。"""
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            logger.exception("未安装 pypdf: {}", exc)
            raise

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("暂不支持加密 PDF")
        if len(reader.pages) > 500:
            raise ValueError("PDF 超过 500 页限制，请拆分文件")
        segments, size = [], 0
        for number, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 10 * 1024 * 1024:
                raise ValueError("PDF 单页内容过大，请拆分文件")
            value = page.extract_text() or ""
            size += len(value)
            if size > self._max_chars:
                raise ValueError("文档文字超过 50 万字符限制，请拆分文件")
            if value.strip():
                segments.append(ParsedSegment(value, page_number=number))
        return ParsedDocument(
            text="\n".join(part.text for part in segments),
            mime_type=mime,
            meta={"pages": len(reader.pages), "page_numbers_available": True},
            segments=segments,
        )

    def _parse_docx(self, data: bytes, mime: str) -> ParsedDocument:
        """Read body paragraphs and tables in order; never fetch links or run macros."""
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                items = archive.infolist()
                if len(items) > 2048 or sum(item.file_size for item in items) > 100 * 1024 * 1024:
                    raise ValueError("DOCX 解压大小或文件数量超过限制")
                if len({item.filename for item in items}) != len(items):
                    raise ValueError("DOCX 包含重复条目")
                for item in items:
                    name = item.filename.replace("\\", "/")
                    if (
                        ".." in name.split("/")
                        or name.startswith("/")
                        or "vbaproject" in name.casefold()
                    ):
                        raise ValueError("DOCX 包含宏或异常路径")
                item = archive.getinfo("word/document.xml")
                if item.file_size > 20 * 1024 * 1024:
                    raise ValueError("DOCX 正文 XML 过大")
                raw = archive.read(item)
        except (BadZipFile, KeyError, RuntimeError, NotImplementedError):
            raise ValueError("不是可读取的 DOCX 文件；旧版 DOC 请先转换") from None
        if b"\x00" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("DOCX 包含不支持的 XML 编码或实体声明")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            raise ValueError("DOCX 正文 XML 已损坏") from None
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        prefix = "{" + ns["w"] + "}"
        body = root.find("w:body", ns)
        if body is None:
            raise ValueError("DOCX 缺少正文")
        segments, section, size = [], None, 0
        for paragraph in body.iter(prefix + "p"):
            pieces = []
            for node in paragraph.iter():
                if node.tag == prefix + "t":
                    pieces.append(node.text or "")
                elif node.tag in (prefix + "br", prefix + "cr"):
                    pieces.append("\n")
                elif node.tag == prefix + "tab":
                    pieces.append("\t")
            value = "".join(pieces).strip()
            size += len(value)
            if size > self._max_chars:
                raise ValueError("文档文字超过 50 万字符限制，请拆分文件")
            style = paragraph.find("w:pPr/w:pStyle", ns)
            if style is not None and re.match(
                r"^(Heading|标题)", style.get(prefix + "val", ""), re.I
            ):
                section = value[:255]
            if value:
                segments.append(ParsedSegment(value, section=section))
        return ParsedDocument(
            text="\n\n".join(part.text for part in segments),
            mime_type=mime,
            meta={
                "page_numbers_available": False,
                "warning": "Word 页码依赖排版，按章节和片段定位；表格按正文读取，计算请导出 CSV/XLSX",
            },
            segments=segments,
        )
