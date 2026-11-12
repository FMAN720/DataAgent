from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.analysis.workspace import WorkspaceConflict, WorkspaceError, WorkspaceStore
from app.analytics.semantic_tools import GetBusinessDefinitionTool
from app.analytics.tools import RunSQLTool
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.main import create_app
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_semantic_metrics import finance_profile, semantic_env  # noqa: F401


def _query():
    return reply(
        "",
        tool_call(
            "query_business_metric", metric_id="income", start="2025-12-01", end="2026-01-01"
        ),
    )


def make_agent(env, script):
    catalog, query_tool, _path = env
    registry = ToolRegistry()
    registry.register(GetBusinessDefinitionTool())
    registry.register(query_tool)
    registry.register(RunSQLTool(query_tool.database))
    llm = ScriptedLLM(script)
    agent = AgentOrchestrator(
        llm,
        registry,
        InMemorySessionStore(),
        system_prompt="SYSTEM",
        enable_reflection=False,
        workspace=catalog.store,
        semantic_catalog=catalog,
    )
    return agent, llm, registry


def test_persisted_stages_evidence_and_response(semantic_env):
    agent, _llm, _registry = make_agent(semantic_env, [_query(), reply("实收收入：323.45 元。")])
    response = run(agent.run("12月实收收入？", business_profile_id="finance"))
    assert response.success and response.validation["status"] == "passed"
    assert response.business_profile["id"] == "finance"
    stored = WorkspaceStore(semantic_env[0].store.path).get_task(response.task_id)
    assert stored["status"] == "completed" and not stored["resumable"]
    assert all(status == "completed" for status in stored["stages"].values())
    events = [event["event"] for event in stored["events"]]
    assert any(event.get("tool") == "query_business_metric" for event in events)
    assert all("thought" not in event for event in events)
    assert stored["response"]["validation"] == response.validation
    assert all("thought" not in step for step in stored["response"]["steps"])


def test_selected_profile_cannot_escape_to_arbitrary_sql(semantic_env, monkeypatch):
    agent, _llm, registry = make_agent(
        semantic_env,
        [
            reply("", tool_call("run_sql", sql="SELECT * FROM receipts")),
            _query(),
            reply("实收收入：323.45 元。"),
        ],
    )
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    response = run(agent.run("分析收入", business_profile_id="finance"))
    assert response.success and not response.steps[0].ok
    assert [call.args[0] for call in invoke.await_args_list] == ["query_business_metric"]


def _cancel(_messages, _kwargs):
    raise asyncio.CancelledError()


def cancelled_task(env):
    agent, llm, _registry = make_agent(env, [_query(), _cancel])
    with pytest.raises(asyncio.CancelledError):
        run(agent.run("12月收入", session_id="same-session", business_profile_id="finance"))
    task = env[0].store.list_tasks()[0]
    assert task["status"] == "cancelled" and len(llm.calls) == 2
    return task


def test_explicit_resume_requeries_current_data_and_keeps_attempts(semantic_env):
    task = cancelled_task(semantic_env)
    with closing(sqlite3.connect(semantic_env[2])) as conn, conn:
        conn.execute("UPDATE receipts SET amount_cents=10000 WHERE receipt_id=1")
    agent, _llm, _registry = make_agent(semantic_env, [_query(), reply("实收收入：300.00 元。")])
    response = run(
        agent.run(
            task["question"],
            task["session_id"],
            business_profile_id="finance",
            resume_task_id=task["id"],
        )
    )
    assert response.success and response.task_id == task["id"] and response.task_attempt == 2
    record = semantic_env[0].store.get_task(task["id"])
    assert record["status"] == "completed"
    assert {event["attempt"] for event in record["events"]} == {1, 2}
    assert "300" in response.steps[0].output
    assert "323.45" not in response.steps[0].output


def test_recovery_uses_error_feedback_but_requeries_numbers(semantic_env):
    first, _llm, _registry = make_agent(semantic_env, [_query(), reply("实收收入：32,345.00 元。")])
    failed = run(first.run("12月收入", session_id="retry-failure", business_profile_id="finance"))
    assert not failed.success

    def requery(messages, kwargs):
        text = "\n".join(message.get("content", "") for message in messages)
        assert "上次未通过程序校验" in text and "必须重新查询" in text
        assert "323.45" not in text
        return _query()

    second, _llm, _registry = make_agent(semantic_env, [requery, reply("实收收入：323.45 元。")])
    recovered = run(
        second.run(
            "12月收入",
            "retry-failure",
            business_profile_id="finance",
            resume_task_id=failed.task_id,
        )
    )
    assert recovered.success and recovered.task_attempt == 2


def test_changed_profile_blocks_resume_before_model_call(semantic_env):
    task = cancelled_task(semantic_env)
    semantic_env[0].save(finance_profile(), 1)
    agent, llm, _registry = make_agent(semantic_env, [])
    with pytest.raises(WorkspaceConflict, match="业务口径"):
        run(
            agent.run(
                task["question"],
                task["session_id"],
                business_profile_id="finance",
                resume_task_id=task["id"],
            )
        )
    assert not llm.calls


def test_journal_failure_stops_before_next_model_request(semantic_env, monkeypatch):
    agent, llm, _registry = make_agent(semantic_env, [_query(), reply("not reached")])

    record = semantic_env[0].store.record_event

    def unavailable(identifier, owner, event):
        if event.get("type") == "tool":
            raise WorkspaceError("offline write failure")
        return record(identifier, owner, event)

    monkeypatch.setattr(semantic_env[0].store, "record_event", unavailable)
    with pytest.raises(WorkspaceError):
        run(agent.run("12月收入", business_profile_id="finance"))
    assert len(llm.calls) == 1
    assert semantic_env[0].store.list_tasks()[0]["status"] == "failed"


def test_stale_lease_is_interrupted_without_automatic_execution(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.db")
    task = store.start_task("q", "s", "react", {"id": "x"}, 0)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute("UPDATE analysis_tasks SET heartbeat=0 WHERE id=?", (task["id"],))
    assert WorkspaceStore(store.path).get_task(task["id"])["status"] == "interrupted"
    resumed = store.start_task("q", "s", "react", {"id": "x"}, 0, resume_id=task["id"])
    assert resumed["attempt"] == 2
    with pytest.raises(WorkspaceConflict):
        store.heartbeat(task["id"], task["owner"])
    with pytest.raises(WorkspaceConflict):
        store.start_task("q", "s", "react", {"id": "x"}, 0, resume_id=task["id"])


def test_context_change_blocks_resume(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.db")
    task = store.start_task("q", "s", "react", {"id": "x"}, 3)
    store.finish_task(task["id"], task["owner"], "cancelled")
    resumed = store.start_task("q", "s", "react", {"id": "x"}, 3, resume_id=task["id"])
    with pytest.raises(WorkspaceConflict, match="会话条件"):
        store.bind_context(task["id"], resumed["owner"], 4)


def test_session_binding_prevents_cross_business_history_even_without_postgres(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace.db")
    first = store.start_task("q", "session", "react", {"id": "finance", "revision": 1}, 0)
    store.finish_task(first["id"], first["owner"], "completed")
    with pytest.raises(WorkspaceConflict, match="业务口径"):
        store.start_task("q2", "session", "react", {"id": "stock", "revision": 1}, 0)
    store.clear_binding("session")
    second = store.start_task("q2", "session", "react", {"id": "stock", "revision": 1}, 0)
    assert second["id"] != first["id"]


def test_profile_validation_never_echoes_accidentally_pasted_secret(semantic_env):
    app = create_app()
    with TestClient(app) as client:
        app.state.workspace_store = semantic_env[0].store
        app.state.semantic_catalog = semantic_env[0]
        body = {
            "expected_revision": 1,
            "profile": finance_profile().model_dump(),
            "api_key": "offline-secret-must-not-echo",
        }
        response = client.put("/api/v1/business-profiles/finance", json=body)
        assert response.status_code == 422
        assert "offline-secret-must-not-echo" not in response.text
        assert (
            client.put(
                "/api/v1/business-profiles/finance",
                content=b"x" * 131073,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )


def test_local_profile_http_versioning_and_no_automatic_calls(semantic_env):
    catalog, _tool, _path = semantic_env
    app = create_app()
    with TestClient(app) as client:
        app.state.workspace_store = catalog.store
        app.state.semantic_catalog = catalog
        assert client.get("/api/v1/business-profiles").status_code == 200
        assert (
            client.get(
                "/api/v1/business-profiles", headers={"Host": "rebinding.invalid"}
            ).status_code
            == 403
        )
        old = client.get("/api/v1/business-profiles/finance?revision=1").json()
        saved = client.put(
            "/api/v1/business-profiles/finance",
            json={"expected_revision": 1, "profile": finance_profile().model_dump()},
        )
        assert saved.status_code == 200 and saved.json()["revision"] == 2
        assert client.get("/api/v1/business-profiles/finance?revision=1").json() == old
        assert (
            client.put(
                "/api/v1/business-profiles/finance",
                json={"expected_revision": 1, "profile": finance_profile().model_dump()},
            ).status_code
            == 409
        )
        assert (
            client.get(
                "/api/v1/business-profiles", headers={"Origin": "https://other.invalid"}
            ).status_code
            == 403
        )
        assert client.get("/api/v1/analysis-tasks").json() == {"tasks": []}


def test_tampered_profile_revision_is_rejected_before_execution(semantic_env):
    store = semantic_env[0].store
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute(
            "UPDATE business_profiles SET definition=? WHERE id='finance'",
            (json.dumps({"id": "changed"}),),
        )
    with pytest.raises(WorkspaceError, match="版本校验失败"):
        semantic_env[0].snapshot("finance")


def test_http_resume_is_explicit_and_uses_saved_task(semantic_env):
    task = cancelled_task(semantic_env)
    agent, llm, _registry = make_agent(semantic_env, [_query(), reply("实收收入：323.45 元。")])
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = agent
        app.state.workspace_store = semantic_env[0].store
        app.state.semantic_catalog = semantic_env[0]
        endpoint = f"/api/v1/analysis-tasks/{task['id']}/resume"
        assert client.post(endpoint, json={"confirmed": False}).status_code == 422
        assert client.post(endpoint, json={"confirmed": 1}).status_code == 422
        assert client.post(endpoint, json={"confirmed": "true"}).status_code == 422
        assert not llm.calls
        result = client.post(endpoint, json={"confirmed": True})
        assert result.status_code == 200 and result.json()["task_attempt"] == 2
        assert client.post(endpoint, json={"confirmed": True}).status_code == 409
        assert len(llm.calls) == 2
