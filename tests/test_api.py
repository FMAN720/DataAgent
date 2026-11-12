"""HTTP 层：/chat、/chat/stream、/traces，以及未配置 Key 时的 503。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.main import create_app
from tests.conftest import ScriptedLLM, reply, tool_call


@pytest.fixture()
def client_factory(registry):
    def make(script):
        app = create_app()
        client = TestClient(app)
        client.__enter__()  # 触发 lifespan
        app.state.agent = AgentOrchestrator(
            ScriptedLLM(script),
            registry,
            InMemorySessionStore(),
            system_prompt="SYSTEM",
            enable_reflection=False,
        )
        return client

    clients = []

    def wrapped(script):
        c = make(script)
        clients.append(c)
        return c

    yield wrapped
    for c in clients:
        c.__exit__(None, None, None)


def test_chat_returns_answer_and_steps(client_factory) -> None:
    client = client_factory(
        [
            reply("", tool_call("run_sql", sql="select count(*) from regions")),
            reply("共有 5 个区域"),
        ]
    )
    r = client.post("/api/v1/chat", json={"question": "有几个区域"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "共有 5 个区域"
    assert body["steps"][0]["tool"] == "run_sql"
    assert json.loads(body["steps"][0]["output"])["rows"] == [[5]]

    trace = client.get(f"/api/v1/traces/{body['trace_id']}").json()
    assert [s["operation"] for s in trace["spans"]] == ["agent.run", "tool.run_sql"]


def test_chat_stream_emits_events_in_order(client_factory) -> None:
    client = client_factory([reply("", tool_call("list_tables")), reply("完成")])
    with client.stream("POST", "/api/v1/chat/stream", json={"question": "有哪些表"}) as r:
        events = [
            json.loads(line[len("data: ") :])
            for line in r.iter_lines()
            if line.startswith("data: ")
        ]
    assert [e["type"] for e in events] == ["start", "llm", "tool", "llm", "final"]
    assert events[-1]["answer"] == "完成"


def test_chat_without_agent_returns_503() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.agent = None
        app.state.agent_error = "Agent 初始化失败: 未配置 OPENAI_API_KEY"
        r = client.post("/api/v1/chat", json={"question": "hi"})
    assert r.status_code == 503
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_request_validation(client_factory) -> None:
    client = client_factory([])
    assert client.post("/api/v1/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/v1/chat", json={"question": "x", "mode": "bad"}).status_code == 422
