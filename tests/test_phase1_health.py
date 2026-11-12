"""核心与可选依赖的健康状态，以及未启用文档库时的错误边界。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import health
from app.config import Settings, get_settings
from app.infrastructure.database import session as database_session
from app.main import create_app


@pytest.mark.parametrize(
    "agent_ok,analysis_ok,documents_ok,status_code,state",
    [
        (False, False, False, 503, "unavailable"),
        (True, False, True, 503, "unavailable"),
        (True, True, False, 200, "degraded"),
        (True, True, True, 200, "ready"),
    ],
)
def test_readiness_checks_core_separately_from_documents(
    monkeypatch, agent_ok, analysis_ok, documents_ok, status_code, state
):
    app = create_app()
    check = AsyncMock(side_effect=None if analysis_ok else RuntimeError("analysis down"))
    app.state.agent = (
        SimpleNamespace(
            check_ready=check, check_session_ready=AsyncMock(), session_backend="memory"
        )
        if agent_ok
        else None
    )
    app.state.document_engine = object() if documents_ok else None
    document_check = AsyncMock()
    monkeypatch.setattr(health, "check_document_storage", document_check)
    client = TestClient(app)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == status_code
    assert response.json()["status"] == state
    assert response.json()["analysis_database"] == ("up" if agent_ok and analysis_ok else "down")
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    client.close()


def test_document_database_failure_is_degraded_not_500(monkeypatch):
    app = create_app()
    app.state.agent = SimpleNamespace(
        check_ready=AsyncMock(), check_session_ready=AsyncMock(), session_backend="memory"
    )
    app.state.document_engine = object()
    monkeypatch.setattr(
        health, "check_document_storage", AsyncMock(side_effect=RuntimeError("down"))
    )
    client = TestClient(app)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    client.close()


@pytest.mark.parametrize("path", ["/api/v1/documents", "/api/v1/documents/upload"])
def test_documents_without_storage_returns_503(path):
    app = create_app()
    app.state.document_session_factory = None
    app.state.document_error = "文档服务未启用"
    client = TestClient(app)
    if path.endswith("upload"):
        response = client.post(path, files={"file": ("notes.txt", b"notes", "text/plain")})
    else:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json()["detail"] == "文档服务未启用"
    client.close()


def test_stale_global_session_does_not_enable_documents_for_another_app(monkeypatch):
    stale_factory = AsyncMock()
    monkeypatch.setattr(database_session, "async_session_factory", stale_factory)
    client = TestClient(create_app())
    assert client.get("/api/v1/documents").status_code == 503
    stale_factory.assert_not_called()
    client.close()


def test_disabled_document_feature_does_not_initialize_database(monkeypatch):
    import app.main as main

    fake_agent = SimpleNamespace(aclose=AsyncMock())
    init = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(main, "build_agent", lambda settings, **kwargs: fake_agent)
    monkeypatch.setattr(database_session, "init_engine", init)
    settings = Settings(_env_file=None, documents_enabled=False)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    app = main.create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        assert client.get("/api/v1/documents").status_code == 503
    init.assert_not_called()
    fake_agent.aclose.assert_awaited_once()
