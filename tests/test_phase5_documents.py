"""Document parsing, explicit provenance and scoped APIs; no remote model calls."""

import io
import json
from datetime import date
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.etl.parser import DocumentParser
from app.etl.pipeline import ETLPipeline
from app.knowledge.service import document_conditions
from app.knowledge.types import EmbeddingError, KnowledgeError
from app.main import create_app
from eval.phase3_live import BudgetTransport
from eval.phase5_cases import cases, corpus
from tests.conftest import run


def docx_bytes(xml=None):
    stream = io.BytesIO()
    if xml is None:
        xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>活动规则</w:t></w:r></w:p>
          <w:p><w:r><w:t>补贴在十一月底结束。</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>金额</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>12.30</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          </w:body></w:document>"""
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def pdf_bytes(*pages, encrypted=False):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=500, height=700)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        contents = DecodedStreamObject()
        contents.set_data(f"BT /F1 12 Tf 20 600 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(contents)
    if encrypted:
        writer.encrypt("synthetic-only")
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_markdown_sections_survive_chunking():
    result = run(
        ETLPipeline().run_bytes(
            "# 标题\n前言\n## 活动时间\n十一月底结束。\n## 口径\n保留有效订单。".encode(),
            "note.md",
            "text/markdown",
        )
    )
    assert len(result.chunks) == len(result.chunk_metadata) == 3
    assert [part["section"] for part in result.chunk_metadata] == ["标题", "活动时间", "口径"]
    assert all(part["page_number"] is None for part in result.chunk_metadata)


def test_docx_body_table_order_and_section_provenance():
    result = run(ETLPipeline().run_bytes(docx_bytes(), "note.docx", "application/octet-stream"))
    assert "活动规则\n\n补贴在十一月底结束。\n\n金额\n\n12.30" == result.parsed.text
    assert all(part["section"] == "活动规则" for part in result.chunk_metadata)
    assert not result.parsed.meta["page_numbers_available"]
    assert all(part["page_number"] is None for part in result.chunk_metadata)


@pytest.mark.parametrize(
    "raw",
    [
        b"not a zip",
        docx_bytes('<!DOCTYPE root [<!ENTITY secret "x">]><root/>'),
        docx_bytes("<broken"),
        docx_bytes("<root/>"),
        docx_bytes("<root/>".encode("utf-16")),
    ],
)
def test_bad_docx_is_rejected(raw):
    with pytest.raises(ValueError):
        DocumentParser().parse_bytes(raw, "bad.docx", None)


def test_docx_archive_bound_and_macros(monkeypatch):
    import app.etl.parser as parser

    class Archive:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def infolist(self):
            return [SimpleNamespace(file_size=101 * 1024 * 1024)]

    monkeypatch.setattr(parser, "ZipFile", lambda *args: Archive())
    with pytest.raises(ValueError, match="解压"):
        DocumentParser().parse_bytes(b"anything", "bad.docx", None)


def test_pdf_pages_are_not_merged_or_silently_dropped():
    result = run(
        ETLPipeline().run_bytes(
            pdf_bytes("Page one policy", "Page two evidence"), "policy.pdf", "application/pdf"
        )
    )
    assert result.chunk_metadata == [
        {"page_number": 1, "section": None},
        {"page_number": 2, "section": None},
    ]
    assert "Page two evidence" in result.chunks[1]
    with pytest.raises(ValueError, match="加密"):
        DocumentParser().parse_bytes(pdf_bytes("Secret", encrypted=True), "policy.pdf", None)
    with pytest.raises(ValueError, match="字符"):
        DocumentParser(max_chars=3).parse_bytes(pdf_bytes("Too long"), "policy.pdf", None)


def test_document_filters_reject_invalid_ranges_and_types():
    for kwargs in (
        {"date_from": "2025-13-01"},
        {"date_from": 2025},
        {"date_from": "2025-12-01", "date_to": "2025-01-01"},
        {"document_type": "sql"},
    ):
        with pytest.raises(KnowledgeError):
            document_conditions(**kwargs)
    assert (
        len(
            document_conditions(
                date_from=date(2025, 1, 1), date_to="2025-12-31", document_type="review"
            )
        )
        == 4
    )


def test_api_shell_is_local_and_excludes_secrets(monkeypatch):
    from unittest.mock import AsyncMock

    import app.main as main

    app = create_app()
    settings = Settings(
        _env_file=None,
        documents_enabled=False,
        datasets_enabled=False,
        embedding_enabled=False,
        openai_api_key="synthetic-secret-chat",
        embedding_api_key="synthetic-secret-embedding",
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "build_agent", lambda _: SimpleNamespace(aclose=AsyncMock()))
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, raise_server_exceptions=True) as client:
        # No real startup connection or model client is initialized.
        response = client.get("/")
        assert response.status_code == 200 and "Content-Security-Policy".lower() in response.headers
        assert "数据分析助手" in response.text
        assert client.get("/assets/workspace.js").status_code == 200
        config = client.get("/workspace-config").json()
        assert not any("key" in name.lower() or "url" in name.lower() for name in config)
        assert "synthetic-secret" not in json.dumps(config)


def test_synthetic_splits_and_corpus_are_fixed_and_disjoint():
    questions = cases()
    assert len(questions) == len({item["query"] for item in questions}) == 60
    assert sum(item["split"] == "calibration" for item in questions) == 30
    assert sum(item["split"] == "holdout" for item in questions) == 30
    assert sum(item["expected"] is None for item in questions) == 20
    docs = corpus()
    assert len(docs) == len({doc["id"] for doc in docs}) == 15
    assert {item["expected"] for item in questions if item["expected"]} <= {
        doc["id"] for doc in docs
    }


def test_budget_ledger_survives_restart_and_blocks_unapproved_text(tmp_path):
    async def scenario():
        sent = []

        async def respond(request):
            sent.append(request)
            return httpx.Response(500)

        ledger = tmp_path / "ledger.json"
        for _ in range(2):
            transport = BudgetTransport(
                httpx.MockTransport(respond),
                max_requests=2,
                max_characters=10,
                ledger_path=ledger,
                allowed_inputs={"合成文本"},
            )
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    json={"model": "BAAI/bge-m3", "input": ["合成文本"]},
                )
        restored = BudgetTransport(
            httpx.MockTransport(respond),
            max_requests=2,
            max_characters=10,
            ledger_path=ledger,
            allowed_inputs={"合成文本"},
        )
        async with httpx.AsyncClient(transport=restored) as client:
            with pytest.raises(EmbeddingError, match="budget_exceeded"):
                await client.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    json={"model": "BAAI/bge-m3", "input": ["合成文本"]},
                )
            with pytest.raises(EmbeddingError, match="allowlist"):
                await client.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    json={"model": "BAAI/bge-m3", "input": ["用户上传正文"]},
                )
        assert len(sent) == 2 and restored.characters == 8

    run(scenario())
