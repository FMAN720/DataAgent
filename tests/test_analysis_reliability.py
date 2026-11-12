"""数据可靠性：真实只读 SQLite、脏数据、固定指标及 Agent 控制流；不调用 API。"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.analytics.evidence import collect_evidence
from app.analytics.metrics import GetMetricDefinitionTool, QueryMetricTool
from app.analytics.quality import ProfileDatasetTool
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.react_agent import ReActAgent, ToolStep
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.main import create_app
from tests.conftest import GLOSSARY, ScriptedLLM, reply, run, tool_call

REQUEST = {
    "metric_id": "net_sales",
    "source": "ecommerce_v1",
    "start": "2025-01-01",
    "end": "2025-01-04",
}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "quality.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE users(user_id INTEGER, is_test INTEGER, region_code TEXT);
            CREATE TABLE orders(
                order_id INTEGER,
                user_id INTEGER,
                order_time TEXT,
                status TEXT,
                channel TEXT,
                pay_amount INTEGER -- 实付金额，单位：分
            );
            INSERT INTO users VALUES (1,0,'NORTH'),(2,1,'EAST'),(3,0,'SOUTH');
            INSERT INTO orders VALUES
              (1,1,'2025-01-01 12:00:00','paid','app',10009),
              (2,1,'2025-01-02 12:00:00','refunded','web',2000),
              (3,1,'2025-01-03 12:00:00','cancelled','app',0),
              (4,1,'2025-01-03 15:00:00','completed','app',15001),
              (5,2,'2025-01-03 12:00:00','paid','app',99999),
              (6,3,'2025-01-01 12:00:00','paid','app',1000);
            CREATE TABLE uploaded(id INTEGER, amount NUMERIC, occurred TEXT, label TEXT);
            INSERT INTO uploaded VALUES
              (1,10.25,'2025-01-01','A'), (1,-2.5,'2025-01-02',' '),
              (2,NULL,'2025-01-03',NULL), (3,'invalid','2025-01-03','B');
        """)
    database = AnalyticsDatabase("sqlite:///" + path.as_posix())
    registry = ToolRegistry()
    for tool in build_analytics_tools(database, GlossaryStore.from_file(GLOSSARY)):
        registry.register(tool)
    yield path, database, registry
    database.dispose()


def mutate(source, statement):
    with sqlite3.connect(source[0]) as conn:
        conn.executescript(statement)


def metric(environment, **changes):
    result = run(environment[2].invoke("query_metric", {**REQUEST, **changes}))
    assert result.ok, result.content
    return json.loads(result.content)


def test_fixed_catalog_is_versioned_and_does_not_claim_actual_values():
    data = run(GetMetricDefinitionTool().execute())
    assert {definition["id"] for definition in data["definitions"]} == {
        "net_sales",
        "order_count",
        "refund_rate",
    }
    assert all(
        definition["version"] == "1" and definition["exclude_test_users"]
        for definition in data["definitions"]
    )
    assert "summary" not in data


def test_net_sales_excludes_test_users_and_refunds_and_keeps_cents(source):
    data = metric(source)
    assert data["status"] == "ready" and data["summary"]["value"] == "260.1"
    assert data["metric"]["unit"] == "元"
    assert "order_items" not in " ".join(data["queries"])
    assert data["quality"]["order_rows"] == 6


def test_existing_demo_gold_net_sales_is_unchanged(registry):
    result = run(
        registry.invoke("query_metric", {**REQUEST, "start": "2025-11-01", "end": "2025-12-01"})
    )
    data = json.loads(result.content)
    assert result.ok and data["summary"]["value"] == "1047258.23"


def test_order_count_and_refund_rate_have_fixed_different_denominators(source):
    assert metric(source, metric_id="order_count")["summary"]["value"] == 3
    data = metric(source, metric_id="refund_rate")
    assert data["summary"] == {"numerator": 1, "denominator": 4, "value": "25"}


def test_refund_requests_cannot_change_refund_metric(source):
    mutate(
        source,
        "CREATE TABLE refund_requests(order_id INTEGER,status TEXT); INSERT INTO refund_requests VALUES (1,'rejected'),(4,'rejected');",
    )
    assert metric(source, metric_id="refund_rate")["summary"]["value"] == "25"


def test_registered_comparison_is_reconciled_and_uses_identical_filters(source):
    data = metric(
        source,
        start="2025-01-02",
        end="2025-01-04",
        base_start="2025-01-01",
        base_end="2025-01-02",
        dimension="region",
    )
    comparison = data["comparison"]
    assert comparison["reconciliation"]["matched"]
    assert comparison["summary"]["base"] == "110.09"
    assert comparison["summary"]["current"] == "150.01"
    assert comparison["summary"]["delta"] == "39.92"
    assert any("天数不同" in warning for warning in comparison["warnings"])
    assert all("u.is_test=0" in sql for sql in data["queries"])


def test_metric_filters_are_applied_to_both_group_and_total(source):
    data = metric(source, region_code="NORTH", order_channel="app", dimension="order_channel")
    assert data["summary"]["value"] == "250.1"
    assert all(
        "u.region_code='NORTH'" in sql and "o.channel='app'" in sql for sql in data["queries"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "uploads.custom"},
        {"source": ""},
        {"unit": "万元"},
        {"start": ""},
        {"base_start": "2024-12-01"},
        {"start": "2025-01-01", "end": "2025-02-01"},
    ],
)
def test_unconfirmed_source_unit_or_incomplete_period_has_no_result(source, changes):
    data = metric(source, **changes)
    assert data["status"] == "needs_clarification" and data["result"] is None and data["questions"]


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2025-02-30"},
        {"start": "20250101"},
        {"end": "2025-01-01"},
        {"base_start": "2025-01-02", "base_end": "2025-01-05"},
        {"region_code": "NORTH' OR 1=1 --"},
        {"sql": "SELECT 999"},
        {"detail_sql": "SELECT 999"},
        {"formula": "SUM(total_amount)"},
        {"statuses": ["cancelled"]},
    ],
)
def test_free_sql_formula_and_invalid_scope_cannot_enter_fixed_metric(source, changes):
    result = run(source[2].invoke("query_metric", {**REQUEST, **changes}))
    assert not result.ok


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("INSERT INTO users VALUES (1,0,'NORTH')", "用户键重复"),
        ("INSERT INTO orders SELECT * FROM orders WHERE order_id=1", "订单键重复"),
        ("UPDATE orders SET user_id=999 WHERE order_id=1", "无法关联"),
        ("UPDATE orders SET pay_amount=NULL WHERE order_id=1", "金额"),
        ("UPDATE orders SET pay_amount=-1 WHERE order_id=1", "金额"),
        ("UPDATE orders SET pay_amount=10.5 WHERE order_id=1", "金额"),
        ("UPDATE orders SET pay_amount='bad' WHERE order_id=1", "金额"),
        ("UPDATE orders SET status='unknown' WHERE order_id=1", "未知订单状态"),
        ("UPDATE orders SET status=NULL WHERE order_id=1", "关键字段缺失"),
        ("UPDATE users SET is_test=NULL WHERE user_id=1", "测试账号"),
        ("UPDATE orders SET order_time='2025-02-30 12:00:00' WHERE order_id=1", "标准时间"),
        ("UPDATE orders SET order_time='not a date' WHERE order_id=1", "标准时间"),
        ("DELETE FROM orders", "订单表为空"),
    ],
)
def test_dirty_critical_data_blocks_metric_before_statistics(source, statement, expected):
    mutate(source, statement)
    executed = []
    event.listen(
        source[1]._engine,
        "before_cursor_execute",
        lambda conn, cursor, sql, params, context, many: executed.append(sql),
    )
    data = metric(source)
    assert data["status"] == "needs_clarification" and data["result"] is None
    assert expected in " ".join(data["questions"])
    assert not any("AS segment" in sql for sql in executed), (
        "statistics must not run after rejected quality check"
    )


def test_unknown_money_unit_is_not_automatically_divided_by_100(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE orders(order_id INTEGER,user_id INTEGER,order_time TEXT,status TEXT,channel TEXT,pay_amount INTEGER); CREATE TABLE users(user_id INTEGER,is_test INTEGER,region_code TEXT);"
        )
    database = AnalyticsDatabase("sqlite:///" + path.as_posix())
    try:
        data = run(QueryMetricTool(database).execute(**REQUEST))
        assert data["status"] == "needs_clarification" and "禁止自动除以100" in data["questions"][0]
    finally:
        database.dispose()


def test_zero_denominator_is_undefined_and_not_zero_percent(source):
    mutate(source, "UPDATE orders SET status='cancelled',pay_amount=0")
    data = metric(source, metric_id="refund_rate")
    assert data["status"] == "ready" and data["summary"]["value"] is None
    assert any("未定义" in warning for warning in data["warnings"])


def test_empty_filter_is_not_reported_as_business_zero(source):
    data = metric(source, region_code="WEST")
    assert data["status"] == "needs_clarification" and data["result"] is None


def test_truncated_groups_are_rejected(source):
    source[1].max_rows = 1
    result = run(source[2].invoke("query_metric", {**REQUEST, "dimension": "region"}))
    assert not result.ok and "截断" in result.content


def test_profile_reports_missing_duplicates_ranges_without_altering_source(source):
    data = run(
        ProfileDatasetTool(source[1]).execute(
            table="uploaded",
            columns=["id", "amount", "label"],
            key_columns=["id"],
            date_column="occurred",
        )
    )
    assert data["row_count"] == 4 and data["duplicate_excess_rows"] == 1
    assert data["status"] == "needs_clarification"
    amount = next(column for column in data["columns"] if column["column"] == "amount")
    assert amount["null_count"] == 1 and amount["invalid_numeric_count"] == 1
    assert amount["min"] == -2.5 and amount["max"] == 10.25 and amount["negative_count"] == 1
    label = next(column for column in data["columns"] if column["column"] == "label")
    assert label["blank_count"] == 1 and label["missing_pct"] == 50
    assert data["date_range"]["completeness"] == "unverified"
    with sqlite3.connect(source[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM uploaded").fetchone()[0] == 4
        assert conn.execute("SELECT amount FROM uploaded WHERE amount<0").fetchone()[0] == -2.5


def test_profile_date_coverage_only_warns_about_observed_boundaries(source):
    result = run(
        source[2].invoke(
            "profile_dataset",
            {
                "table": "uploaded",
                "date_column": "occurred",
                "expected_start": "2025-01-01",
                "expected_end": "2025-02-01",
            },
        )
    )
    data = json.loads(result.content)
    assert data["status"] == "needs_clarification" and any(
        "末日" in item for item in data["questions"]
    )
    assert any("不能证明" in item for item in data["warnings"])


@pytest.mark.parametrize(
    "changes",
    [
        {"table": "uploaded; DROP TABLE users"},
        {"columns": ["SUM(amount)"]},
        {"key_columns": ["unknown"]},
        {"columns": ["id", "id"]},
        {"date_column": "occurred", "expected_start": "2025-01-01"},
        {"columns": [str(i) for i in range(9)]},
    ],
)
def test_profile_accepts_only_real_identifiers_and_bounded_valid_scope(source, changes):
    result = run(source[2].invoke("profile_dataset", {"table": "uploaded", **changes}))
    assert not result.ok


def test_profile_handles_quoted_identifiers(source):
    mutate(source, 'CREATE TABLE "a""b"("c""d" INTEGER); INSERT INTO "a""b" VALUES (7);')
    data = run(ProfileDatasetTool(source[1]).execute(table='a"b', columns=['c"d']))
    assert data["row_count"] == 1 and data["columns"][0]["min"] == 7


def test_profile_empty_table_asks_for_source_data(source):
    mutate(source, "DELETE FROM uploaded")
    data = run(ProfileDatasetTool(source[1]).execute(table="uploaded"))
    assert data["status"] == "needs_clarification"
    assert data["columns"][0]["missing_pct"] is None


def test_single_missing_business_key_is_not_reported_as_valid_unique(source):
    mutate(source, "UPDATE uploaded SET id=NULL WHERE id=3")
    data = run(ProfileDatasetTool(source[1]).execute(table="uploaded", key_columns=["id"]))
    assert data["key_missing_rows"] == 1
    assert any("无法可靠标识" in item for item in data["questions"])


def test_money_comment_containing_fentan_does_not_mean_cents(source, monkeypatch):
    original = source[1].describe

    async def describe(names):
        tables, missing = await original(names)
        for table in tables:
            for column in table.columns:
                if column.name == "pay_amount":
                    column.comment = "实付金额，未分摊优惠，单位：元"
        return tables, missing

    monkeypatch.setattr(source[1], "describe", describe)
    data = metric(source)
    assert data["status"] == "needs_clarification" and data["result"] is None


@pytest.mark.parametrize(
    "control,args",
    [
        ("query_metric", {**REQUEST, "unit": "万元"}),
        ("profile_dataset", {"table": "uploaded", "key_columns": ["id"]}),
    ],
)
def test_quality_control_stops_same_batch_sql_and_returns_clarification(
    source, monkeypatch, control, args
):
    calls = []
    original = source[2].invoke

    async def invoking(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    monkeypatch.setattr(source[2], "invoke", invoking)
    llm = ScriptedLLM(
        [reply("", tool_call("run_sql", sql="SELECT 999"), tool_call(control, **args))]
    )
    data = run(ReActAgent(llm, source[2]).run([{"role": "user", "content": "分析"}]))
    assert data.needs_clarification and calls == [control]
    assert len(llm.calls) == 1 and not data.steps[0].ok


def test_data_quality_messages_and_fixed_comparison_reach_api_and_sse(source):
    for streaming in (False, True):
        llm = ScriptedLLM(
            [
                reply(
                    "",
                    tool_call(
                        "query_metric",
                        **{
                            **REQUEST,
                            "start": "2025-01-02",
                            "base_start": "2025-01-01",
                            "base_end": "2025-01-02",
                            "dimension": "region",
                        },
                    ),
                ),
                reply("净销售额增加39.92元，完整性尚未验证。"),
            ]
        )
        instance = AgentOrchestrator(
            llm, source[2], InMemorySessionStore(), system_prompt="SYSTEM", enable_reflection=False
        )
        app = create_app()
        with TestClient(app) as client:
            app.state.agent = instance
            response = client.post(
                "/api/v1/chat" + ("/stream" if streaming else ""), json={"question": "比较净销售额"}
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
        assert (
            payload["success"]
            and payload["analysis"]["comparisons"][0]["summary"]["delta"] == "39.92"
        )
        assert payload["analysis"]["metrics"][0]["metric"]["version"] == "1"
        assert any("不证明" in warning for warning in payload["analysis"]["warnings"])


def test_blocked_quality_is_visible_in_analysis_and_not_hidden_by_clarification(source):
    llm = ScriptedLLM(
        [reply("", tool_call("profile_dataset", table="uploaded", key_columns=["id"]))]
    )
    instance = AgentOrchestrator(llm, source[2], InMemorySessionStore(), system_prompt="SYSTEM")
    data = run(instance.run("检查数据"))
    assert data.needs_clarification
    assert data.analysis["quality_checks"][0]["duplicate_excess_rows"] == 1
    assert any("重复" in warning for warning in data.analysis["warnings"])


def test_new_evidence_is_structured_and_marks_truncation_incomplete():
    for name in ("profile_dataset", "get_metric_definition", "query_metric"):
        packet = collect_evidence([ToolStep(1, name, {}, True, '{"status":"ready"}', 1)])
        assert json.loads(packet.records[0])["output"] == {"status": "ready"}
        assert packet.valid_ids == {"E1"}


def test_snapshot_validator_stops_later_queries_and_connection_recovers(source):
    seen = []

    def reject(result):
        seen.append(result.rows)
        raise ValueError("reject")

    with pytest.raises(ValueError, match="reject"):
        run(source[1].execute_snapshot(["SELECT 1", "SELECT 2", "SELECT 3"], validate_first=reject))
    assert seen == [[[1]]]
    assert run(source[1].execute("SELECT COUNT(*) FROM orders")).rows == [[6]]


def test_profile_failure_precedes_even_an_earlier_metric_in_same_batch(source, monkeypatch):
    calls = []
    original = source[2].invoke

    async def invoking(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    monkeypatch.setattr(source[2], "invoke", invoking)
    llm = ScriptedLLM(
        [
            reply(
                "",
                tool_call("query_metric", **REQUEST),
                tool_call("profile_dataset", table="uploaded", key_columns=["id"]),
            )
        ]
    )
    result = run(ReActAgent(llm, source[2]).run([{"role": "user", "content": "检查后分析"}]))
    assert result.needs_clarification and calls == ["profile_dataset"]
    assert not result.steps[0].ok


def test_maximum_profile_output_stays_complete_json_under_tool_limit(source):
    columns = [f"long_column_{i}" for i in range(8)]
    mutate(
        source,
        "CREATE TABLE wide("
        + ",".join(name + " NUMERIC" for name in columns)
        + "); INSERT INTO wide VALUES (NULL,NULL,NULL,NULL,-1,-1,-1,-1);",
    )
    result = run(source[2].invoke("profile_dataset", {"table": "wide", "columns": columns}))
    assert result.ok and len(result.content) < 8000
    assert len(json.loads(result.content)["columns"]) == 8
