"""Offline acceptance for frozen fixtures, independent grading and API budget guards."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from eval.phase7_budget import BudgetedTransport, EvaluationBudgetExceeded, EvaluationLedger
from eval.phase7_cases import ABLATION_IDS, CASES
from eval.phase7_fixtures import ScopedKnowledge, build_evaluation_agent, prepare
from eval.phase7_grader import grade, numbers
from eval.run_phase7 import BoundedLLM, exclusive_run, summarize
from tests.conftest import ScriptedLLM, reply, run, tool_call


def payload(**changes):
    return {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "synthetic"}],
        "max_tokens": 2048,
        **changes,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://evil.invalid/v1/chat/completions"},
        {"url": "http://api.deepseek.com/v1/chat/completions"},
        {"url": "https://api.deepseek.com/v1/chat/completions?forward=true"},
        {"body": payload(model="unapproved-model")},
        {"body": payload(max_tokens=2049)},
        {"body": payload(max_tokens=True)},
        {"body": payload(stream=True)},
        {"body": payload(messages=[{"role": "user", "content": "fake-key-secret"}])},
        {"body": payload(messages=[{"role": "user", "content": "x" * 100_001}])},
    ],
)
def test_transport_rejects_unapproved_requests_before_sending(tmp_path, change):
    sent = []

    async def scenario():
        ledger = EvaluationLedger(tmp_path / "ledger.json", "fixed")
        transport = BudgetedTransport(
            httpx.MockTransport(lambda request: sent.append(request)),
            ledger,
            "chat",
            "deepseek-chat",
            secrets=["fake-key-secret"],
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(EvaluationBudgetExceeded):
                await client.post(
                    change.get("url", "https://api.deepseek.com/v1/chat/completions"),
                    json=change.get("body", payload()),
                )
        assert not ledger.data["requests"]
        assert transport.blocked

    run(scenario())
    assert not sent


def test_persistent_ledger_preserves_unknown_usage_and_rejects_reset(tmp_path):
    async def scenario():
        path = tmp_path / "ledger.json"
        ledger = EvaluationLedger(path, "fixed", max_chat_requests=1)
        await ledger.reserve("chat", json.dumps(payload()))
        resumed = EvaluationLedger(path, "fixed", max_chat_requests=1)
        assert Decimal(resumed.data["requests"][0]["ceiling_cny"]) > 0
        with pytest.raises(EvaluationBudgetExceeded):
            await resumed.reserve("chat", "synthetic")
        with pytest.raises(ValueError):
            EvaluationLedger(path, "changed", max_chat_requests=1)
        with pytest.raises(ValueError):
            EvaluationLedger(path, "fixed", max_chat_requests=2)

    run(scenario())


def test_concurrent_calls_cannot_exceed_http_cap(tmp_path):
    async def scenario():
        ledger = EvaluationLedger(tmp_path / "ledger.json", "fixed", max_chat_requests=2)
        results = await asyncio.gather(
            *(ledger.reserve("chat", "synthetic") for _ in range(10)), return_exceptions=True
        )
        assert sum(isinstance(value, int) for value in results) == 2
        assert sum(isinstance(value, EvaluationBudgetExceeded) for value in results) == 8

    run(scenario())


def test_cost_character_and_embedding_limits_are_independent(tmp_path):
    async def scenario():
        cheap = EvaluationLedger(tmp_path / "cost.json", "fixed", max_cny=0.001)
        with pytest.raises(EvaluationBudgetExceeded):
            await cheap.reserve("chat", "small")
        chars = EvaluationLedger(tmp_path / "chars.json", "fixed", max_characters=3)
        await chars.reserve("embedding", "abc")
        with pytest.raises(EvaluationBudgetExceeded):
            await chars.reserve("chat", "d")
        embed = EvaluationLedger(tmp_path / "embedding.json", "fixed", max_embedding_requests=1)
        await embed.reserve("embedding", "text")
        with pytest.raises(EvaluationBudgetExceeded):
            await embed.reserve("embedding", "text")
        await embed.reserve("chat", "text")

    run(scenario())


def test_returned_usage_settles_reservation_but_anomaly_stops_further_spend(tmp_path):
    async def scenario():
        ledger = EvaluationLedger(tmp_path / "ledger.json", "fixed")
        index = await ledger.reserve("chat", "small")
        await ledger.complete(
            index,
            httpx.Response(200, json={"usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
        )
        assert Decimal(ledger.data["requests"][0]["ceiling_cny"]) == Decimal("0.00016")
        index = await ledger.reserve("chat", "small")
        await ledger.complete(
            index,
            httpx.Response(
                200, json={"usage": {"prompt_tokens": 999999, "completion_tokens": 2048}}
            ),
        )
        with pytest.raises(EvaluationBudgetExceeded):
            await ledger.reserve("chat", "small")

    run(scenario())


def test_no_double_run_and_no_false_live_metrics(tmp_path):
    with exclusive_run(tmp_path):
        with pytest.raises(RuntimeError, match="Another"):
            with exclusive_run(tmp_path):
                pass
    with exclusive_run(tmp_path):
        pass
    report = summarize(tmp_path)
    assert report["live_status"] == "not_run" and report["completed_runs"] == 0
    assert report["variants"] == {}


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    return prepare(tmp_path_factory.mktemp("phase7"))


def test_fifty_cases_have_disjoint_fixed_splits_and_independent_sql(prepared):
    manifest, database = prepared
    assert len(CASES) == 50 and len(ABLATION_IDS) == 10
    dev = {case["id"] for case in CASES if case["split"] == "development"}
    hold = {case["id"] for case in CASES if case["split"] == "holdout"}
    assert len(dev) == 30 and len(hold) == 20 and not dev & hold
    assert len(manifest["gold"]) == 31
    assert manifest["gold"]["data_id"] == [["001"]]
    assert manifest["gold"]["data_sum"] == [[14.9]]
    assert manifest["gold"]["data_null"] == [[1]]
    assert all(manifest["gold"][key][0][0] > 0 for key in ("sql_nov_net", "sql_dec_net"))
    assert prepare(database.parents[1])[0]["fingerprint"] == manifest["fingerprint"]


def answer(value, *, source=None):
    return {
        "answer": value,
        "success": True,
        "citation_errors": [],
        "citations": [],
        "steps": []
        if source is None
        else [{"tool": "run_sql", "ok": True, "output": json.dumps({"rows": [[source]]})}],
    }


def test_correct_number_without_actual_evidence_does_not_pass():
    case = CASES[0]
    assert not grade(case, [[123.45]], answer("123.45元"), {})["passed"]
    assert not grade(case, [[123.45]], answer("123.45元", source=999), {})["passed"]
    assert not grade(case, [[123.45]], answer("-123.45元", source=123.45), {})["passed"]
    assert grade(case, [[123.45]], answer("123.45元", source=123.45), {})["passed"]
    assert numbers("金额12.3万元") == [Decimal("123000")]


def test_fixed_metric_and_calculator_outputs_count_as_evidence():
    case = CASES[0]

    def tool(name, output):
        response = answer("1047258.23 元")
        response["steps"] = [{"tool": name, "ok": True, "output": json.dumps(output)}]
        return response

    single = {"status": "ready", "summary": {"value": "1047258.23"}, "groups": []}
    assert grade(case, [[1047258.23]], tool("query_metric", single), {})["passed"]
    compared = {"status": "ready", "comparison": {"summary": {"current": "1047258.23"}}}
    assert grade(case, [[1047258.23]], tool("query_metric", compared), {})["passed"]
    assert grade(case, [[1047258.23]], tool("calculator", 1047258.23), {})["passed"]
    # 需要澄清时工具没有给出数值，仍不能算有证据
    gated = {"status": "needs_clarification", "questions": ["请补充周期"]}
    assert not grade(case, [[1047258.23]], tool("query_metric", gated), {})["passed"]


def test_citation_must_match_required_source_and_validated_chunk():
    case = next(case for case in CASES if case["id"] == "doc_subsidy")
    response = answer("于2025年11月30日结束")
    response["citations"] = [{"document_id": "foreign"}]
    assert not grade(case, None, response, {"appliances": "owned"})["passed"]
    response["citations"] = [{"document_id": "owned"}]
    assert grade(case, None, response, {"appliances": "owned"})["passed"]
    response["citation_errors"] = ["fabricated reference"]
    assert not grade(case, None, response, {"appliances": "owned"})["passed"]


def test_comparison_requires_reconciliation_and_clarification_stops_queries():
    case = next(case for case in CASES if case["id"] == "cmp_net")
    response = answer("10元变为20元", source=10)
    response["steps"].append(
        {"tool": "run_sql", "ok": True, "output": json.dumps({"rows": [[20]]})}
    )
    assert not grade(case, [[10, 20]], response, {})["passed"]
    response["steps"].append(
        {
            "tool": "compare_periods",
            "ok": True,
            "output": json.dumps({"reconciliation": {"matched": True}}),
        }
    )
    assert grade(case, [[10, 20]], response, {})["passed"]
    clarify = next(case for case in CASES if case["id"] == "safe_time")
    response = answer("请提供两个周期。", source=10)
    assert not grade(clarify, None, response, {})["passed"]


def test_scoped_search_cannot_read_other_documents():
    class Service:
        async def search(self, query, **kwargs):
            return kwargs

    async def scenario():
        scoped = ScopedKnowledge(Service(), {"own"}, "keyword")
        assert await scoped.search("q") == {"document_ids": ["own"], "mode": "keyword"}
        with pytest.raises(ValueError):
            await scoped.search("q", document_ids=["foreign"])

    run(scenario())


def test_production_agent_queries_only_synthetic_dataset_and_no_gold_in_prompt(prepared):
    manifest, database = prepared

    async def scenario():
        llm = ScriptedLLM(
            [
                reply("", tool_call("list_datasets")),
                reply(
                    "", tool_call("run_sql", sql="SELECT ROUND(SUM(amount),2) FROM uploaded_budget")
                ),
                reply("合计14.90元"),
            ]
        )
        knowledge = SimpleNamespace(service=None, owned=set())
        agent = build_evaluation_agent(llm, database, manifest, knowledge, "no_reflection")
        try:
            response = await agent.run("合成预算补充表合计多少元？")
            assert (
                response.success
                and json.loads(response.steps[0].output)["datasets"][0]["row_count"] == 4
            )
            assert json.loads(response.steps[1].output)["rows"] == [[14.9]]
            assert "14.9" not in llm.calls[0][0][0]["content"]
        finally:
            await agent.aclose()

    run(scenario())


def test_output_bound_applies_to_planner_and_review_calls():
    llm = ScriptedLLM([reply("ok")])
    run(BoundedLLM(llm).chat([], max_tokens=8000))
    assert llm.calls[0][1]["max_tokens"] == 2048
