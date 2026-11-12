"""Clarification, evidence integrity, bounded revisions and HTTP/SSE contracts; no API."""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.analytics.evidence import collect_evidence
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.react_agent import ToolStep
from app.core.agent.reflection import ReflectionAgent
from app.core.memory.session import InMemorySessionStore
from app.knowledge.types import SearchResponse
from app.main import create_app
from app.models.enums import ReflectionStatus
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_phase3_citations import make_hit
from tests.test_phase6_comparison import SCOPE


def review(claim="查询结果是 1", ref="E1", status="supported", **fields):
    return json.dumps(
        {
            "passed": True,
            "quality_score": 95,
            "checks": [
                {
                    "claim": claim,
                    "status": status,
                    "evidence_ids": [ref] if ref else [],
                    "reason": "按本轮查询核对",
                }
            ],
            **fields,
        }
    )


def agent(registry, script, **kwargs):
    return AgentOrchestrator(
        ScriptedLLM(script), registry, InMemorySessionStore(), system_prompt="SYSTEM", **kwargs
    )


def test_clarification_stops_same_batch_sql_and_is_saved_for_followup(registry, monkeypatch):
    original = registry.invoke
    calls = []

    async def invoke(name, args):
        calls.append(name)
        return await original(name, args)

    monkeypatch.setattr(registry, "invoke", invoke)

    async def scenario():
        instance = agent(
            registry,
            [
                reply(
                    "",
                    tool_call("run_sql", sql="SELECT 999"),
                    tool_call("prepare_analysis", scope={"metric": "销售额"}),
                )
            ],
        )
        answer = await instance.run("销售额为何变化？", session_id="clarification")
        assert answer.needs_clarification and answer.success and answer.reflection is None
        assert answer.analysis["status"] == "needs_clarification"
        assert calls == ["prepare_analysis"] and not answer.steps[0].ok
        assert "基准" in answer.answer and "当前周期" in answer.answer
        history = await instance._sessions.get_history("clarification")
        assert history[-1]["content"] == answer.answer

    run(scenario())


def test_invalid_scope_blocks_same_batch_sql_and_model_can_correct(registry, monkeypatch):
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    instance = agent(
        registry,
        [
            reply(
                "",
                tool_call("prepare_analysis", scope={**SCOPE, "base_start": "wrong"}),
                tool_call("run_sql", sql="SELECT 999"),
            ),
            reply("请核对基期日期"),
        ],
        enable_reflection=False,
    )
    answer = run(instance.run("q"))
    assert answer.success
    assert [call.args[0] for call in invoke.await_args_list] == ["prepare_analysis"]


def test_clarification_followup_retains_question_and_resolves_scope(registry):
    def resolve(messages, kwargs):
        assert any("开始分析前" in message.get("content", "") for message in messages)
        assert messages[-1]["content"] == "用演示库，单位元，按区域，比较11月和12月"
        return reply("", tool_call("prepare_analysis", scope=SCOPE))

    async def scenario():
        instance = agent(
            registry,
            [
                reply("", tool_call("prepare_analysis", scope={"metric": "销售额"})),
                resolve,
                reply(
                    "",
                    tool_call(
                        "compare_periods",
                        scope=SCOPE,
                        detail_sql="SELECT '华东' AS segment, 100 AS base_value, 80 AS current_value",
                        total_sql="SELECT 100 AS base_value, 80 AS current_value",
                    ),
                ),
                reply("下降20元，因果待核实"),
                reply(review("下降20元", "E2")),
            ],
        )
        first = await instance.run("销售额为什么下降", session_id="followup")
        assert first.needs_clarification
        second = await instance.run(
            "用演示库，单位元，按区域，比较11月和12月", session_id=first.session_id
        )
        assert not second.needs_clarification and second.analysis["status"] == "reviewed"

    run(scenario())


def test_multiple_scope_confirmations_do_not_race_or_select_an_arbitrary_scope(
    registry, monkeypatch
):
    invoke = AsyncMock(wraps=registry.invoke)
    monkeypatch.setattr(registry, "invoke", invoke)
    instance = agent(
        registry,
        [
            reply(
                "",
                tool_call("prepare_analysis", scope=SCOPE),
                tool_call("prepare_analysis", scope={}),
                tool_call("run_sql", sql="SELECT 999"),
            ),
            reply("请先明确一个分析范围"),
        ],
        enable_reflection=False,
    )
    result = run(instance.run("q"))
    assert result.success and all(not step.ok for step in result.steps)
    invoke.assert_not_called()


def test_scope_and_real_comparison_survive_http_and_sse(registry):
    for streaming in (False, True):
        instance = agent(
            registry,
            [
                reply("", tool_call("prepare_analysis", scope=SCOPE)),
                reply(
                    "",
                    tool_call(
                        "compare_periods",
                        scope=SCOPE,
                        detail_sql="SELECT '华东' AS segment, 100 AS base_value, 80 AS current_value",
                        total_sql="SELECT 100 AS base_value, 80 AS current_value",
                    ),
                ),
                reply("净销售额下降 20 元，变化集中在华东。原因尚未验证。"),
                reply(review("销售额下降 20 元", "E2")),
            ],
        )
        app = create_app()
        app.state.agent = instance
        with TestClient(app) as client:
            app.state.agent = instance
            response = client.post(
                "/api/v1/chat" + ("/stream" if streaming else ""), json={"question": "比较变化"}
            )
            payload = (
                [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ][-1]
                if streaming
                else response.json()
            )
        assert payload["analysis"]["status"] == "reviewed"
        assert payload["analysis"]["comparisons"][0]["summary"]["delta"] == "-20"
        assert len(payload["steps"]) == 2 and payload["session_id"]
        assert payload["reflection"]["checks"][0]["evidence_ids"] == ["E2"]


@pytest.mark.parametrize("status", ["conflicting", "insufficient"])
def test_conflicting_or_unsupported_claim_cannot_pass_high_score(status):
    report = run(
        ReflectionAgent(ScriptedLLM([reply(review(status=status))])).reflect(
            "q", "answer", ["evidence"], valid_evidence_ids={"E1"}, require_claim_checks=True
        )
    )
    assert report.status == ReflectionStatus.FAILED and report.issues


@pytest.mark.parametrize("ref", ["E999", "", "D:invented"])
def test_review_refs_must_be_successful_included_tool_records(ref):
    report = run(
        ReflectionAgent(ScriptedLLM([reply(review(ref=ref))])).reflect(
            "q", "answer", ["evidence"], valid_evidence_ids={"E1"}, require_claim_checks=True
        )
    )
    assert report.status == ReflectionStatus.FAILED and "证据编号" in report.issues[0]


def test_summary_only_review_does_not_claim_full_validation():
    report = run(
        ReflectionAgent(ScriptedLLM([reply('{"passed":true,"quality_score":99}')])).reflect(
            "q", "answer", ["evidence"], require_claim_checks=True
        )
    )
    assert report.status == ReflectionStatus.UNAVAILABLE and report.error == "MissingClaimChecks"


def test_evidence_keeps_complete_sql_and_results_and_never_slices_packets():
    steps = [
        ToolStep(1, "run_sql", {"sql": "SELECT 123"}, True, '{"rows":[[123]],"columns":["n"]}', 0),
        ToolStep(2, "run_sql", {"sql": "SELECT 999"}, False, '{"error":"failed"}', 0),
    ]
    bundle = collect_evidence(steps)
    assert bundle.complete and bundle.valid_ids == {"E1"}
    assert json.loads(bundle.records[0])["arguments"]["sql"] == "SELECT 123"
    small = collect_evidence(steps, budget=150)
    assert not small.complete and small.omitted
    for record in small.records:
        if record.startswith("{"):
            assert isinstance(json.loads(record), dict)


def test_truncated_json_or_output_can_never_be_complete_evidence():
    for text in ('{"rows":', '{"rows":[]}\n...[输出过长，已截断，原长度 9000 字符]'):
        bundle = collect_evidence([ToolStep(1, "run_sql", {}, True, text, 0)])
        assert not bundle.complete and not bundle.valid_ids


def test_budget_omission_overrides_a_model_pass():
    report = run(
        ReflectionAgent(ScriptedLLM([reply(review())])).reflect(
            "q", "answer", ["X" * 15000], valid_evidence_ids={"E1"}, require_claim_checks=True
        )
    )
    assert report.status == ReflectionStatus.UNAVAILABLE
    assert report.error == "IncompleteEvidence" and not report.evidence_complete


def test_failed_final_review_is_visible_and_does_not_pollute_session(registry):
    async def scenario():
        instance = agent(
            registry,
            [
                reply("", tool_call("run_sql", sql="SELECT 1")),
                reply("结果 999"),
                reply(review(status="conflicting")),
                reply("仍然是 999"),
                reply(review(status="conflicting")),
            ],
        )
        answer = await instance.run("q", session_id="failed")
        assert (
            answer.success and answer.revised
        )  # Generation success is separate from evidence status.
        assert answer.reflection_status == ReflectionStatus.FAILED
        assert answer.analysis["status"] == "review_failed" and answer.analysis["warnings"]
        assert not await instance._sessions.get_history("failed")
        assert not instance._react._llm.llm.script  # Exactly one revision, no unbounded retries.

    run(scenario())


def test_unavailable_review_is_not_saved_as_verified_context(registry):
    async def scenario():
        instance = agent(
            registry,
            [reply("", tool_call("run_sql", sql="SELECT 1")), reply("结果 1"), reply("bad")],
        )
        answer = await instance.run("q", session_id="unavailable")
        assert answer.analysis["status"] == "review_unavailable"
        assert not await instance._sessions.get_history("unavailable")

    run(scenario())


def test_review_sees_sql_scope_and_causal_boundaries(registry):
    def inspect(messages, kwargs):
        prompt = messages[0]["content"]
        assert "因果" in prompt and "百分点" in prompt and "冲突" in prompt
        assert (
            "SELECT 123" in messages[1]["content"]
            and '"evidence_id": "E1"' in messages[1]["content"]
        )
        return reply(review("结果是 123"))

    result = run(
        agent(
            registry,
            [reply("", tool_call("run_sql", sql="SELECT 123")), reply("结果是 123"), inspect],
        ).run("q")
    )
    assert result.reflection_status == ReflectionStatus.PASSED


def test_contradictory_documents_keep_both_sources_and_do_not_choose_by_rank(registry):
    weekly = make_hit("合成周报：按实付金额统计，销售额下降 10%。活动影响尚未验证。")
    draft = make_hit("合成草稿：按原价金额统计，销售额增长 20%。未经复核。")

    class Service:
        async def search(self, *args, **kwargs):
            return SearchResponse(mode="keyword", hits=[draft, weekly])

    async def scenario():
        instance = agent(
            registry,
            [
                reply("", tool_call("search_documents", query="销售额变化")),
                reply(
                    f"两份资料口径冲突：实付下降与原价增长不能直接比较，暂不能确认活动影响。[{weekly.reference}] [{draft.reference}]"
                ),
                reply(review("资料统计口径不同，因果影响尚未验证")),
            ],
        )
        instance.enable_document_search(Service())
        answer = await instance.run("活动为什么影响销售？")
        assert answer.success and len(answer.citations) == 2
        assert {citation.reference for citation in answer.citations} == {
            weekly.reference,
            draft.reference,
        }
        assert answer.reflection_status == ReflectionStatus.PASSED

    run(scenario())


def test_empty_document_search_can_support_an_explicit_insufficiency_answer(registry):
    class Service:
        async def search(self, *args, **kwargs):
            return SearchResponse(mode="keyword", hits=[])

    async def scenario():
        instance = agent(
            registry,
            [
                reply("", tool_call("search_documents", query="未提供的活动证据")),
                reply("当前资料不足以回答。"),
                reply(review("本轮资料不足，不能判断活动因果")),
            ],
        )
        instance.enable_document_search(Service())
        answer = await instance.run("活动导致下降了吗？")
        assert answer.success and not answer.citations and not answer.citation_errors
        assert answer.reflection_status == ReflectionStatus.PASSED

    run(scenario())
