"""使用模拟异步存储测试上传边界，不依赖 PostgreSQL 或 PDF 解析包。"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.api.routes import document
from app.config import Settings, get_settings
from app.etl.parser import DocumentParser, ParsedDocument
from app.etl.pipeline import ETLResult
from app.infrastructure.database.session import get_async_session
from app.main import create_app
from tests.conftest import run


class FakeSession:
    def __init__(self, error=None):
        self.items = []
        self.committed = False
        self.rolled_back = False
        self.error = error

    def add(self, item):
        self.items.append(item)

    async def commit(self):
        if self.error:
            raise self.error
        self.committed = True

    async def rollback(self):
        self.rolled_back = True
        self.items.clear()


@pytest.fixture()
def upload_client(tmp_path):
    settings = Settings(_env_file=None, upload_dir=tmp_path / "uploads", upload_max_bytes=1024)
    store = FakeSession()
    app = create_app()

    async def storage():
        yield store

    app.dependency_overrides[get_async_session] = storage
    app.dependency_overrides[get_settings] = lambda: settings
    # 不进入 lifespan；存储已显式注入，测试不初始化真实数据库或模型。
    client = TestClient(app)
    yield client, store, settings
    client.close()


@pytest.mark.parametrize(
    "filename", ["../../escape.txt", "..\\..\\escape.txt", "C:\\temp\\escape.txt"]
)
def test_untrusted_filename_never_controls_storage_path(upload_client, filename):
    client, store, settings = upload_client
    response = client.post(
        "/api/v1/documents/upload", files={"file": (filename, b"business notes", "text/plain")}
    )
    assert response.status_code == 200
    assert store.committed
    doc = store.items[0]
    path = Path(doc.storage_path)
    assert path.parent == settings.upload_dir.resolve()
    assert path.name == f"{doc.id}.txt"
    assert doc.filename == "escape.txt"
    assert path.read_bytes() == b"business notes"


def test_markdown_with_generic_mime_is_parsed(upload_client):
    client, store, _ = upload_client
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.md", "# 标题\n内容".encode(), "application/octet-stream")},
    )
    assert response.status_code == 200
    assert store.items[1].content.startswith("# 标题")


@pytest.mark.parametrize(
    "filename,raw,mime,status",
    [
        ("x.exe", b"notes", "text/plain", 415),
        ("x.txt", b"notes", "application/pdf", 415),
        ("x.pdf", b"%PDF-1.4", "text/plain", 415),
        ("x.pdf", b"not a pdf", "application/pdf", 422),
        ("x.txt", b"%PDF-1.4", "text/plain", 422),
        ("x.txt", b"PK\x03\x04", "text/plain", 422),
        ("x.txt", b"binary\x00content", "text/plain", 422),
        ("x.txt", b"\xff\xfe", "text/plain", 422),
        ("x.txt", b"", "text/plain", 422),
        ("x.txt", b" \n\t", "text/plain", 422),
        ("x.txt", b"x" * 1025, "text/plain", 413),
    ],
)
def test_invalid_upload_leaves_no_file_or_database_record(
    upload_client, filename, raw, mime, status
):
    client, store, settings = upload_client
    response = client.post("/api/v1/documents/upload", files={"file": (filename, raw, mime)})
    assert response.status_code == status
    assert not store.items and not store.committed
    assert not settings.upload_dir.exists()


def test_missing_pdf_dependency_returns_503_without_installing(upload_client, monkeypatch):
    client, store, settings = upload_client
    monkeypatch.setattr(document.importlib.util, "find_spec", lambda name: None)
    response = client.post(
        "/api/v1/documents/upload", files={"file": ("notes.pdf", b"%PDF-1.4\n", "application/pdf")}
    )
    assert response.status_code == 503
    assert "pypdf" in response.json()["detail"]
    assert not store.items and not settings.upload_dir.exists()


@pytest.mark.parametrize("empty", [False, True])
def test_failed_or_empty_parsing_does_not_store_upload(upload_client, monkeypatch, empty):
    client, store, settings = upload_client
    parser = AsyncMock(side_effect=ValueError("damaged"))
    if empty:
        parser = AsyncMock(return_value=ETLResult([], ParsedDocument("", "text/plain", {}), {}))
    monkeypatch.setattr(document.ETLPipeline, "run_bytes", parser)
    response = client.post(
        "/api/v1/documents/upload", files={"file": ("notes.txt", b"notes", "text/plain")}
    )
    assert response.status_code == 422
    assert not store.items and not settings.upload_dir.exists()


def test_database_failure_rolls_back_and_removes_file(upload_client):
    client, store, settings = upload_client
    store.error = RuntimeError("database disconnected")
    response = client.post(
        "/api/v1/documents/upload", files={"file": ("notes.txt", b"notes", "text/plain")}
    )
    assert response.status_code == 503
    assert store.rolled_back and not store.items
    assert list(settings.upload_dir.iterdir()) == []


def test_rollback_failure_still_removes_file_and_returns_503(upload_client):
    client, store, settings = upload_client
    store.error = RuntimeError("database disconnected")
    store.rollback = AsyncMock(side_effect=RuntimeError("connection already lost"))
    response = client.post(
        "/api/v1/documents/upload", files={"file": ("notes.txt", b"notes", "text/plain")}
    )
    assert response.status_code == 503
    store.rollback.assert_awaited_once()
    assert list(settings.upload_dir.iterdir()) == []


def test_cancelled_upload_rolls_back_and_removes_file(tmp_path):
    store = FakeSession(asyncio.CancelledError())
    settings = Settings(_env_file=None, upload_dir=tmp_path / "uploads")
    file = UploadFile(filename="notes.txt", file=io.BytesIO(b"notes"))
    with pytest.raises(asyncio.CancelledError):
        run(document.upload_document(file=file, session=store, settings=settings))
    assert store.rolled_back
    assert list(settings.upload_dir.iterdir()) == []


def test_size_limit_stops_reading_at_limit_plus_one_byte():
    contents = io.BytesIO(b"x" * 10000)
    upload = UploadFile(filename="x.txt", file=contents)
    with pytest.raises(HTTPException) as caught:
        run(document._read_upload(upload, 10))
    assert caught.value.status_code == 413
    assert contents.tell() == 11


def test_exclusive_save_does_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "existing.txt"
    path.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        document._save_file(path, b"replacement")
    assert path.read_bytes() == b"original"


def test_markdown_parser_accepts_missing_mime_and_utf8_bom():
    parsed = DocumentParser().parse_bytes(b"\xef\xbb\xbf# Title", "notes.md", None)
    assert parsed.text == "# Title"
