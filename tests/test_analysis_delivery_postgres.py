"""真实 PostgreSQL 历史持久化、跨实例恢复和并发版本栅栏；不调用模型 API。"""

import asyncio
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.analysis.archive import AnalysisArchive, conversation_id
from app.config import Settings
from app.infrastructure.database.manage import load_config
from app.infrastructure.database.models import Conversation, Message
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_analysis_delivery import snapshot

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


@pytest.fixture
def history():
    values = load_config()
    settings = Settings(_env_file=None, database_url=values["DATABASE_URL"])
    archive = AnalysisArchive.from_settings(settings)
    owned = []

    def session_id():
        identifier = "delivery-test-" + uuid.uuid4().hex
        owned.append(identifier)
        return identifier

    try:
        yield archive, session_id, settings
    finally:
        with Session(archive.engine) as session, session.begin():
            ids = [conversation_id(value) for value in owned]
            if ids:
                session.execute(delete(Message).where(Message.conversation_id.in_(ids)))
                session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        run(archive.aclose())


def test_real_postgres_report_survives_independent_archive_clients(history):
    archive, new_id, settings = history
    sid = new_id()
    value = snapshot(sid)
    run(archive.save(value, trusted=True, state={"scope": {"source": "ecommerce_v1"}}, revision=0))
    second = AnalysisArchive.from_settings(settings)
    try:
        assert run(second.report(value["trace_id"]))["archive_status"] == "saved"
        assert run(second.context(sid))["state"]["scope"]["source"] == "ecommerce_v1"
    finally:
        run(second.aclose())


def test_postgres_concurrent_first_completions_cannot_overwrite_conditions(history):
    archive, new_id, settings = history
    sid = new_id()
    second = AnalysisArchive.from_settings(settings)

    async def scenario():
        return await asyncio.gather(
            archive.save(
                snapshot(sid, answer="NORTH"), trusted=True, state={"region": "NORTH"}, revision=0
            ),
            second.save(
                snapshot(sid, answer="SOUTH"), trusted=True, state={"region": "SOUTH"}, revision=0
            ),
        )

    try:
        results = run(scenario())
        assert sum(value["context_applied"] for value in results) == 1
        winner = next(value for value in results if value["context_applied"])
        context = run(archive.context(sid))
        assert context["revision"] == 1 and context["state"] == {"region": winner["answer"]}
        assert len(run(archive.reports(sid))) == 2
    finally:
        run(second.aclose())


def test_clear_context_fences_off_a_pending_completion_but_keeps_reports(history):
    archive, new_id, _ = history
    sid = new_id()
    run(archive.save(snapshot(sid), trusted=True, state={"old": True}, revision=0))
    run(archive.clear_context(sid))
    late = run(archive.save(snapshot(sid), trusted=True, state={"stale": True}, revision=1))
    assert not late["context_applied"]
    assert run(archive.context(sid))["state"] == {}
    assert len(run(archive.reports(sid))) == 2


def test_two_restarted_fastapi_instances_restore_scope_and_export_without_live_llm(
    history, monkeypatch, tmp_path
):
    import app.main as main
    from app.infrastructure.llm import local_settings

    archive, new_id, _ = history
    sid = new_id()
    values = load_config()
    settings = Settings(
        _env_file=None,
        openai_api_key="synthetic-not-used",
        local_model_settings_path=tmp_path / "personal-model.json",
        local_workspace_path=tmp_path / "workspace.sqlite3",
        database_url=values["DATABASE_URL"],
        analytics_db_url=values["ANALYTICS_DB_URL"],
        session_backend="memory",
        analysis_history_enabled=True,
        documents_enabled=False,
        datasets_enabled=False,
        embedding_enabled=False,
        enable_reflection=False,
    )
    common = {
        "metric_id": "net_sales",
        "source": "ecommerce_v1",
        "start": "2025-11-01",
        "end": "2025-12-01",
        "dimension": "region",
    }

    def followup(messages, kwargs):
        assert "上次已确认的分析条件" in messages[0]["content"]
        assert "NORTH" in messages[0]["content"] and "2025-11-01" in messages[0]["content"]
        return reply("", tool_call("query_metric", **common, region_code="SOUTH"))

    class LocalLLM(ScriptedLLM):
        async def aclose(self):
            pass

    scripts = iter(
        [
            LocalLLM(
                [
                    reply("", tool_call("query_metric", **common, region_code="NORTH")),
                    reply("华北合成展示，数值以工具为准"),
                ]
            ),
            LocalLLM([followup, reply("华南合成展示，数值以工具为准")]),
        ]
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(local_settings, "ModelRouter", lambda configs: next(scripts))
    with TestClient(main.create_app()) as client:
        assert (
            client.put(
                "/api/v1/model-settings",
                json={
                    "base_url": "https://offline.test/v1",
                    "model": "synthetic",
                    "api_key": "offline-personal-key",
                },
            ).status_code
            == 200
        )
        first = client.post(
            "/api/v1/chat", json={"question": "华北十一月销售额", "session_id": sid}
        ).json()
        assert (
            first["archive_status"] == "saved"
            and first["analysis_state"]["scope"]["region_code"] == "NORTH"
        )
    with TestClient(main.create_app()) as client:
        restored = client.get("/api/v1/conversations/" + sid).json()
        assert restored["analysis_state"]["scope"]["region_code"] == "NORTH"
        response = client.post(
            "/api/v1/chat/stream", json={"question": "再看看华南，其他条件不变", "session_id": sid}
        )
        final = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ][-1]
        assert (
            final["archive_status"] == "saved"
            and final["analysis_state"]["scope"]["region_code"] == "SOUTH"
        )
        exported = client.get("/api/v1/analyses/" + final["trace_id"] + "/export?format=xlsx")
        assert exported.status_code == 200 and exported.content[:2] == b"PK"
        assert len(client.get("/api/v1/conversations/" + sid).json()["reports"]) == 2
