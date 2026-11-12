"""工具层：schema 导出、错误回传、口径检索。"""

from __future__ import annotations

import json

import pytest

from tests.conftest import run


def test_openai_tool_schema(registry) -> None:
    tools = {t["function"]["name"]: t["function"] for t in registry.to_openai_tools()}
    assert set(tools) == {
        "list_tables",
        "describe_tables",
        "search_glossary",
        "run_sql",
        "calculator",
        "prepare_analysis",
        "compare_periods",
        "profile_dataset",
        "get_metric_definition",
        "query_metric",
    }
    describe = tools["describe_tables"]["parameters"]
    assert describe["properties"]["tables"] == {
        "type": "array",
        "description": describe["properties"]["tables"]["description"],
        "items": {"type": "string"},
    }
    assert tools["run_sql"]["parameters"]["required"] == ["sql"]


def test_describe_includes_comments_and_samples(registry) -> None:
    out = run(registry.invoke("describe_tables", {"tables": ["orders", "no_such"]}))
    assert out.ok
    assert "单位：分" in out.content
    assert "样例" in out.content
    assert "以下表不存在: no_such" in out.content


def test_tool_errors_are_returned_not_raised(registry) -> None:
    cases = [
        ("run_sql", {"sql": "select * from orderz"}, "no such table"),
        ("run_sql", {"sql": "drop table orders"}, "只允许 SELECT"),
        ("run_sql", {}, "缺少必填参数"),
        ("unknown_tool", {}, "未知工具"),
        ("calculator", {"expression": "__import__('os')"}, "表达式无效"),
    ]
    for name, args, expected in cases:
        out = run(registry.invoke(name, args))
        assert not out.ok, name
        assert expected in json.loads(out.content)["error"]


def test_run_sql_returns_structured_rows(registry) -> None:
    out = run(
        registry.invoke(
            "run_sql", {"sql": "select region_code, region_name from regions order by 1"}
        )
    )
    data = json.loads(out.content)
    assert data["columns"] == ["region_code", "region_name"]
    assert ["EAST", "华东"] in data["rows"]


def test_empty_result_hints_model(registry) -> None:
    out = run(
        registry.invoke("run_sql", {"sql": "select * from regions where region_code = 'east'"})
    )
    assert "查询结果为空" in json.loads(out.content)["note"]


@pytest.mark.parametrize(
    "query, expected_title",
    [
        ("退款率", "退款率"),
        ("上个月营收", "净销售额（营收 / 销售额）"),
        ("GMV", "GMV（成交总额）"),
        ("华东地区", "区域"),
        ("月活跃用户", "活跃用户（MAU / DAU）"),
        ("客单价", "客单价（AOV）"),
    ],
)
def test_glossary_retrieval(glossary, query: str, expected_title: str) -> None:
    titles = [e.title for e in run(glossary.search(query, top_k=3))]
    assert expected_title in titles


def _wide_schema_db(tmp_path):
    import sqlite3

    from app.analytics.engine import AnalyticsDatabase

    path = tmp_path / "wide.db"
    conn = sqlite3.connect(path)
    for i in range(40):
        conn.execute(f"CREATE TABLE misc_{i:02d} (id INTEGER, note TEXT)")
    conn.execute("CREATE TABLE refund_requests (request_id INTEGER, order_id INTEGER, reason TEXT)")
    conn.execute("CREATE TABLE user_profiles (user_id INTEGER, region_code TEXT)")
    conn.commit()
    conn.close()
    return AnalyticsDatabase(f"sqlite:///{path.as_posix()}")


def test_list_tables_returns_everything_when_schema_is_small(tmp_path) -> None:
    from app.analytics.tools import ListTablesTool

    db = _wide_schema_db(tmp_path)
    try:
        result = run(ListTablesTool(db, max_tables=100).execute())
        assert len(result["tables"]) == 42
        assert "note" not in result
    finally:
        db.dispose()


def test_list_tables_ranks_by_question_when_schema_is_large(tmp_path) -> None:
    from app.analytics.tools import ListTablesTool

    db = _wide_schema_db(tmp_path)
    try:
        tool = ListTablesTool(db, max_tables=5)
        result = run(tool.execute(question="refund reason"))
        assert result["tables"][0]["table"] == "refund_requests"
        assert len(result["tables"]) == 5 and result["total_tables"] == 42
        assert run(tool.execute(question="region_code"))["tables"][0]["table"] == "user_profiles"
    finally:
        db.dispose()


def test_list_tables_asks_for_question_when_schema_is_large(tmp_path) -> None:
    import pytest

    from app.analytics.tools import ListTablesTool
    from app.core.tools.base import ToolError

    db = _wide_schema_db(tmp_path)
    try:
        with pytest.raises(ToolError, match="question"):
            run(ListTablesTool(db, max_tables=5).execute())
    finally:
        db.dispose()
