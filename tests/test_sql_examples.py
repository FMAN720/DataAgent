"""用户确认的 SQL 示例库：保存条件、去重、召回、工具与接口；SQLite 隔离存储，零付费调用。"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.analysis.archive import AnalysisArchive
from app.analysis.examples import (
    SearchSqlExamplesTool,
    SqlExampleError,
    SqlExampleStore,
    confirmed_sql,
)
from app.infrastructure.database.models import Base, Conversation, Message, SqlExample
from app.main import create_app
from tests.conftest import run


@pytest.fixture
def engine(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "examples.db").as_posix()).execution_options(
        schema_translate_map={"agent": None}
    )
    Base.metadata.create_all(
        engine, tables=[Conversation.__table__, Message.__table__, SqlExample.__table__]
    )
    yield engine
    engine.dispose()


def report(question="11 月净销售额是多少", sql="SELECT 1", **changes):
    value = {
        "trace_id": str(uuid.uuid4()),
        "question": question,
        "reflection_status": "passed",
        "steps": [
            {"tool": "run_sql", "ok": False, "arguments": {"sql": "SELECT broken"}},
            {"tool": "run_sql", "ok": True, "arguments": {"sql": sql}},
        ],
    }
    return {**value, **changes}


def test_confirmed_sql_keeps_only_successful_statements_in_order() -> None:
    value = report()
    value["steps"].append({"tool": "run_sql", "ok": True, "arguments": {"sql": "SELECT 1"}})
    value["steps"].append({"tool": "run_sql", "ok": True, "arguments": {"sql": "SELECT 2"}})
    assert confirmed_sql(value) == ["SELECT 1", "SELECT 2"]


def test_saving_twice_returns_the_same_example(engine) -> None:
    store = SqlExampleStore(engine)
    value = report()
    first = run(store.add_from_report(value, "排除测试账号"))
    second = run(store.add_from_report(value))
    assert first["created"] and not second["created"]
    assert first["id"] == second["id"] and first["sql"] == "SELECT 1"
    assert first["note"] == "排除测试账号"
    assert len(run(store.recent())) == 1


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"reflection_status": "failed"}, "审查未通过"),
        (
            {"steps": [{"tool": "run_sql", "ok": False, "arguments": {"sql": "SELECT x"}}]},
            "没有成功",
        ),
        ({"question": " "}, "缺少原始问题"),
    ],
)
def test_reports_that_cannot_become_examples(engine, changes, message) -> None:
    with pytest.raises(SqlExampleError, match=message):
        run(SqlExampleStore(engine).add_from_report(report(**changes)))


def test_search_ranks_similar_questions_and_tool_hides_numbers(engine) -> None:
    store = SqlExampleStore(engine)
    run(store.add_from_report(report("11 月净销售额是多少", "SELECT SUM(pay_amount) FROM orders")))
    run(store.add_from_report(report("12 月退款率是多少", "SELECT COUNT(*) FROM refunds")))
    hits = run(store.search("10 月退款率"))
    assert hits[0]["sql"] == "SELECT COUNT(*) FROM refunds"

    result = run(SearchSqlExamplesTool(store).execute(question="10 月退款率"))
    assert result["examples"][0]["question"] == "12 月退款率是多少"
    assert set(result["examples"][0]) == {"question", "sql", "note"}
    assert "须重新执行" in result["note"]
    assert run(SearchSqlExamplesTool(store).execute(question="火星矿石"))["examples"] == []


def test_delete_removes_example(engine) -> None:
    store = SqlExampleStore(engine)
    saved = run(store.add_from_report(report()))
    assert run(store.delete(saved["id"]))
    assert not run(store.delete(saved["id"]))
    assert run(store.recent()) == []


def test_http_save_list_delete(engine) -> None:
    archive = AnalysisArchive(engine)
    value = report()
    value.update(
        session_id="s1",
        answer="合成结论",
        analysis={"warnings": []},
        archive_status="disabled",
    )
    saved = run(archive.save(value, trusted=True, state={}, revision=0))
    app = create_app()
    app.state.analysis_archive = archive
    app.state.sql_examples = SqlExampleStore(engine)
    client = TestClient(app)
    created = client.post(
        f"/api/v1/analyses/{saved['trace_id']}/sql-example", json={"note": "备注"}
    )
    assert created.status_code == 201 and created.json()["created"]
    listed = client.get("/api/v1/sql-examples").json()
    assert [item["note"] for item in listed] == ["备注"]
    assert client.delete(f"/api/v1/sql-examples/{listed[0]['id']}").status_code == 204
    assert client.post(f"/api/v1/analyses/{uuid.uuid4()}/sql-example", json={}).status_code == 404


def test_http_returns_503_when_store_missing() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/sql-examples").status_code == 503
