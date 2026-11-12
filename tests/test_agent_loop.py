"""
Agent 循环端到端测试（假 LLM + 真实工具 + 真实演示库）。

覆盖：多步工具调用、SQL 报错后自我修正、步数上限兜底、反思不通过后修正、多轮会话。
"""

from __future__ import annotations

import json

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.react_agent import ReActAgent
from app.core.memory.session import InMemorySessionStore
from app.models.enums import AgentMode
from tests.conftest import ScriptedLLM, reply, run, tool_call

GOOD_SQL = (
    "SELECT ROUND(SUM(o.pay_amount) / 100.0, 2) FROM orders o JOIN users u ON u.user_id = o.user_id "
    "WHERE o.status IN ('paid', 'completed') AND u.is_test = 0 AND o.order_time LIKE '2025-11%'"
)


def _last_tool_output(messages) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "tool")


def test_react_self_corrects_after_sql_error(registry) -> None:
    def check_error_was_seen(messages, kwargs):
        # 模型必须能看到上一条 SQL 的报错，才能修正
        assert "no such column" in _last_tool_output(messages)
        return reply("字段名写错了，改用 pay_amount", tool_call("run_sql", sql=GOOD_SQL))

    def answer_from_result(messages, kwargs):
        value = json.loads(_last_tool_output(messages))["rows"][0][0]
        return reply(f"2025 年 11 月净销售额为 {value:.2f} 元。")

    llm = ScriptedLLM(
        [
            reply("", tool_call("search_glossary", query="销售额")),
            reply("", tool_call("run_sql", sql="SELECT SUM(amount) FROM orders")),
            check_error_was_seen,
            answer_from_result,
        ]
    )
    agent = ReActAgent(llm, registry, max_steps=8)
    result = run(agent.run([{"role": "user", "content": "上个月销售额多少"}]))

    assert result.success
    assert "1047258.23" in result.final_answer
    assert [s.tool for s in result.steps] == ["search_glossary", "run_sql", "run_sql"]
    assert [s.ok for s in result.steps] == [True, False, True]
    # 每个 tool 消息都带上了对应的 tool_call_id
    tool_msgs = [m for m in result.new_messages if m["role"] == "tool"]
    assistant_ids = [
        tc["id"] for m in result.new_messages if m.get("tool_calls") for tc in m["tool_calls"]
    ]
    assert [m["tool_call_id"] for m in tool_msgs] == assistant_ids


def test_parallel_tool_calls_in_one_turn(registry) -> None:
    llm = ScriptedLLM(
        [
            reply(
                "",
                tool_call("search_glossary", query="退款率"),
                tool_call("describe_tables", tables=["orders"]),
            ),
            reply("完成"),
        ]
    )
    result = run(ReActAgent(llm, registry).run([{"role": "user", "content": "q"}]))
    assert [s.tool for s in result.steps] == ["search_glossary", "describe_tables"]
    second_call_messages = llm.calls[1][0]
    assert sum(1 for m in second_call_messages if m["role"] == "tool") == 2


def test_invalid_json_arguments_are_reported_to_model(registry) -> None:
    bad = tool_call("run_sql", sql="x")
    bad.arguments = "{not json"
    llm = ScriptedLLM([reply("", bad), reply("好的")])
    result = run(ReActAgent(llm, registry).run([{"role": "user", "content": "q"}]))
    assert not result.steps[0].ok
    assert "不是合法 JSON" in result.steps[0].output


def test_last_step_forces_answer_without_tools(registry) -> None:
    def must_have_no_tools(messages, kwargs):
        assert "tools" not in kwargs
        assert "上限" in messages[-1]["content"]
        return reply("基于已有数据的结论")

    llm = ScriptedLLM(
        [
            reply("", tool_call("list_tables")),
            reply("", tool_call("list_tables")),
            must_have_no_tools,
        ]
    )
    result = run(ReActAgent(llm, registry, max_steps=3).run([{"role": "user", "content": "q"}]))
    assert result.success
    assert result.llm_calls == 3


def _orchestrator(llm, registry, **kwargs) -> AgentOrchestrator:
    return AgentOrchestrator(
        llm,
        registry,
        InMemorySessionStore(max_turns=5),
        system_prompt="SYSTEM",
        **kwargs,
    )


def test_reflection_failure_triggers_one_revision(registry) -> None:
    def revise(messages, kwargs):
        # 修正轮能看到原始工具结果和审查意见
        assert any(m["role"] == "tool" for m in messages)
        assert "未排除测试账号" in messages[-1]["content"]
        return reply("", tool_call("run_sql", sql=GOOD_SQL))

    llm = ScriptedLLM(
        [
            reply(
                "",
                tool_call(
                    "run_sql",
                    sql="SELECT SUM(pay_amount) FROM orders WHERE order_time LIKE '2025-11%'",
                ),
            ),
            reply("11 月销售额 108 万元"),
            reply(
                json.dumps(
                    {
                        "passed": False,
                        "quality_score": 30,
                        "issues": ["未排除测试账号"],
                        "summary": "口径错误",
                    }
                )
            ),
            revise,
            reply("修正后：11 月净销售额 1047258.23 元"),
            reply(
                json.dumps(
                    {
                        "passed": True,
                        "quality_score": 95,
                        "issues": [],
                        "summary": "修正后口径正确",
                        "checks": [
                            {
                                "claim": "11 月净销售额 1047258.23 元",
                                "status": "supported",
                                "evidence_ids": ["E2"],
                                "reason": "修正后的只读查询返回此金额，已排除测试账号",
                            }
                        ],
                    }
                )
            ),
        ]
    )
    resp = run(_orchestrator(llm, registry).run("上个月销售额", "s1"))

    assert resp.success
    assert resp.revised
    assert resp.answer.startswith("修正后")
    assert len(resp.steps) == 2
    assert resp.reflection is not None and resp.reflection.passed
    assert resp.llm_calls == 6  # 包含两次审查；执行与修正仍为四次。
    assert resp.execution_llm_calls == 4


def test_reflection_skipped_when_no_tools_used(registry) -> None:
    llm = ScriptedLLM([reply("你好，我可以帮你分析销售数据。")])
    resp = run(_orchestrator(llm, registry).run("你好"))
    assert resp.success
    assert resp.reflection is None
    assert len(llm.calls) == 1


def test_multi_turn_history_is_kept(registry) -> None:
    def second_turn(messages, kwargs):
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[2]["content"] == "华东 100 元"
        return reply("华北 80 元")

    llm = ScriptedLLM([reply("华东 100 元"), second_turn])
    orch = _orchestrator(llm, registry, enable_reflection=False)
    first = run(orch.run("华东销售额"))
    second = run(orch.run("那华北呢", first.session_id))
    assert second.answer == "华北 80 元"


def test_plan_mode_injects_plan_into_system_prompt(registry) -> None:
    def check_plan(messages, kwargs):
        assert "分析计划" in messages[0]["content"]
        assert "按品类统计" in messages[0]["content"]
        return reply("结论")

    llm = ScriptedLLM(
        [
            reply(json.dumps({"steps": ["按品类统计 11 月和 12 月销售额", "计算环比"]})),
            check_plan,
        ]
    )
    resp = run(
        _orchestrator(llm, registry, enable_reflection=False).run(
            "哪个品类环比降得最多", mode=AgentMode.PLAN_EXECUTE
        )
    )
    assert resp.plan == ["按品类统计 11 月和 12 月销售额", "计算环比"]


def test_trace_records_tool_spans(registry) -> None:
    llm = ScriptedLLM([reply("", tool_call("list_tables")), reply("ok")])
    orch = _orchestrator(llm, registry, enable_reflection=False)
    resp = run(orch.run("有哪些表"))
    ops = [s.operation for s in orch.tracer.get_trace(resp.trace_id).spans]
    assert ops == ["agent.run", "tool.list_tables"]


def test_fixed_reference_date_is_written_into_prompt() -> None:
    from app.analytics.prompts import build_system_prompt

    prompt = build_system_prompt("sqlite", "2025-12-31")
    assert "今天的日期视为 2025-12-31" in prompt
    assert "{today}" not in prompt


def test_today_reference_date_is_resolved_on_each_request(registry) -> None:
    from datetime import date

    from app.analytics.prompts import build_system_prompt

    def check_date(messages, kwargs):
        assert f"今天的日期视为 {date.today().isoformat()}" in messages[0]["content"]
        assert "{today}" not in messages[0]["content"]
        return reply("结论")

    agent = AgentOrchestrator(
        ScriptedLLM([check_date]),
        registry,
        InMemorySessionStore(),
        system_prompt=build_system_prompt("sqlite", "today"),
        enable_reflection=False,
    )
    assert run(agent.run("今天是几号")).success


def test_reference_date_rejects_other_values() -> None:
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    assert (
        Settings(_env_file=None, analytics_reference_date="today").analytics_reference_date
        == "today"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analytics_reference_date="yesterday")
