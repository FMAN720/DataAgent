"""第一阶段权限与审查回归：脚本模型不访问任何外部 API。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.react_agent import ReActAgent
from app.core.agent.reflection import ReflectionAgent
from app.core.memory.session import InMemorySessionStore
from app.main import create_app
from app.models.enums import ReflectionStatus
from tests.conftest import ScriptedLLM, reply, run, tool_call


@pytest.mark.parametrize("allowed", [[], ["calculator"], ["not_registered"]])
def test_tool_not_in_allowlist_is_never_invoked(registry, monkeypatch, allowed):
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    call = tool_call("run_sql", sql="SELECT 1")
    llm = ScriptedLLM([reply("", call), reply("无法执行该工具")])
    result = run(
        ReActAgent(llm, registry).run(
            [{"role": "user", "content": "q"}],
            tool_names=allowed,
        )
    )
    invoke.assert_not_called()
    assert not result.steps[0].ok
    assert "不允许" in result.steps[0].output
    assert result.new_messages[1]["tool_call_id"] == call.id


@pytest.mark.parametrize("allowed", [None, ["run_sql"]])
def test_allowed_tool_is_executed(registry, monkeypatch, allowed):
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    llm = ScriptedLLM([reply("", tool_call("run_sql", sql="SELECT 1")), reply("结果是 1")])
    result = run(
        ReActAgent(llm, registry).run(
            [{"role": "user", "content": "q"}],
            tool_names=allowed,
        )
    )
    invoke.assert_awaited_once()
    assert result.steps[0].ok


def test_last_chance_does_not_execute_unexpected_tool(registry, monkeypatch):
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    llm = ScriptedLLM([reply("", tool_call("run_sql", sql="SELECT 1"))])
    result = run(ReActAgent(llm, registry, max_steps=1).run([{"role": "user", "content": "q"}]))
    invoke.assert_not_called()
    assert not result.success
    assert not result.steps[0].ok


@pytest.mark.parametrize(
    "payload",
    [
        {"passed": "false", "quality_score": 95},
        {"passed": True, "quality_score": "95"},
        {"passed": True, "quality_score": True},
        {"passed": True, "quality_score": 101},
        {"passed": True, "quality_score": -1},
        {"passed": True},
        {"passed": True, "quality_score": 90, "issues": "wrong type"},
        {"passed": True, "quality_score": 90, "issues": [3]},
        {"passed": True, "quality_score": 90, "summary": 1},
        [],
        None,
    ],
)
def test_invalid_review_is_unavailable(payload):
    llm = ScriptedLLM([reply(json.dumps(payload))])
    report = run(ReflectionAgent(llm).reflect("q", "answer", ["evidence"]))
    assert report.status == ReflectionStatus.UNAVAILABLE
    assert report.passed is None
    assert report.quality_score is None
    assert report.error


@pytest.mark.parametrize(
    "passed,score,status",
    [
        (True, 95, ReflectionStatus.PASSED),
        (True, 20, ReflectionStatus.FAILED),
        (False, 95, ReflectionStatus.FAILED),
    ],
)
def test_review_threshold_and_boolean_are_both_required(passed, score, status):
    llm = ScriptedLLM([reply(json.dumps({"passed": passed, "quality_score": score}))])
    report = run(ReflectionAgent(llm).reflect("q", "answer", ["evidence"]))
    assert report.status == status


def test_review_network_failure_does_not_claim_pass():
    def timeout(messages, kwargs):
        raise TimeoutError("request failed")

    report = run(ReflectionAgent(ScriptedLLM([timeout])).reflect("q", "answer", []))
    assert report.status == ReflectionStatus.UNAVAILABLE
    assert report.error == "TimeoutError"


def _make_agent(registry, script):
    return AgentOrchestrator(
        ScriptedLLM(script),
        registry,
        InMemorySessionStore(),
        system_prompt="SYSTEM",
    )


@pytest.mark.parametrize("streaming", [False, True])
def test_unavailable_review_is_visible_in_http_sse_and_trace(registry, streaming):
    app = create_app()
    agent = _make_agent(
        registry,
        [
            reply("", tool_call("run_sql", sql="SELECT 1")),
            reply("结果是 1"),
            reply("invalid JSON"),
        ],
    )
    app.state.agent = agent
    with TestClient(app) as client:  # 覆盖启动时的真实 Agent，不调用外部模型。
        app.state.agent = agent
        if streaming:
            response = client.post("/api/v1/chat/stream", json={"question": "q"})
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            final = events[-1]
            trace_id = events[0]["trace_id"]
            assert len([e for e in events if e["type"] == "reflection"]) == 1
        else:
            response = client.post("/api/v1/chat", json={"question": "q"})
            final = response.json()
            trace_id = final["trace_id"]
        assert response.status_code == 200
        assert final["success"]
        assert final["reflection_status"] == "unavailable"
        assert final["reflection"]["passed"] is None
        assert final["reflection"]["error"] == "ValueError"
        spans = agent.tracer.get_trace(trace_id).spans
        assert next(s for s in spans if s.operation == "reflection").error == "ValueError"
        assert spans[0].result["reflection_status"] == "unavailable"


def test_revised_answer_is_rechecked_without_unbounded_retry(registry):
    failed = json.dumps({"passed": False, "quality_score": 20, "issues": ["口径错误"]})
    agent = _make_agent(
        registry,
        [
            reply("", tool_call("run_sql", sql="SELECT 1")),
            reply("原答案"),
            reply(failed),
            reply("修正后的答案"),
            reply(failed),
        ],
    )
    result = run(agent.run("q"))
    assert result.success and result.revised
    assert result.answer == "修正后的答案"
    assert result.reflection_status == ReflectionStatus.FAILED
    assert len(agent.tracer.get_trace(result.trace_id).spans) == 4


def test_no_review_is_explicitly_skipped(registry):
    result = run(_make_agent(registry, [reply("请指定时间范围")]).run("销售额"))
    assert result.reflection is None
    assert result.reflection_status == ReflectionStatus.SKIPPED
