"""分析快照、可信状态、导出和 HTTP/SSE；SQLite 隔离存储、脚本模型、零付费调用。"""

import csv
import io
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.analysis.archive import AnalysisArchive, HistoryUnavailable, conversation_id
from app.analysis.artifacts import build_artifacts, confirmed_state
from app.analysis.export import export_report, spreadsheet_text
from app.core.agent.orchestrator import AgentOrchestrator, AgentResponse
from app.core.agent.react_agent import ToolStep
from app.core.agent.reflection import ReflectionReport
from app.core.memory.session import InMemorySessionStore
from app.infrastructure.database.models import Base, Conversation, Message
from app.main import create_app
from app.models.enums import AgentMode
from tests.conftest import ScriptedLLM, reply, run, tool_call
from tests.test_phase6_comparison import SCOPE


@pytest.fixture
def archive(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "history.db").as_posix()).execution_options(
        schema_translate_map={"agent": None}
    )
    Base.metadata.create_all(engine, tables=[Conversation.__table__, Message.__table__])
    yield AnalysisArchive(engine, max_turns=2)
    engine.dispose()


def snapshot(session_id="test-session", **changes):
    response = AgentResponse(
        answer="合成结论",
        success=True,
        trace_id=str(uuid.uuid4()),
        session_id=session_id,
        mode=AgentMode.REACT,
        question="合成问题",
        analysis={"warnings": []},
    )
    return {**response.to_dict(), **changes}


def sql_step(columns, rows, **fields):
    return ToolStep(
        1,
        "run_sql",
        {"sql": "SELECT synthetic"},
        True,
        json.dumps({"columns": columns, "rows": rows, **fields}),
        1,
    )


def comparison_step(matched=True, rate=False):
    scope = {**SCOPE, "metric_kind": "rate" if rate else "additive", "unit": "%" if rate else "元"}
    groups = (
        [
            {"segment": "华东", "base_rate": "10", "current_rate": "15", "contribution_pp": "2"},
            {"segment": "华北", "base_rate": "20", "current_rate": "25", "contribution_pp": "3"},
        ]
        if rate
        else [
            {"segment": "华东", "base_value": "100.25", "current_value": "80.1", "delta": "-20.15"},
            {"segment": "华北", "base_value": "50", "current_value": "70", "delta": "20"},
        ]
    )
    return ToolStep(
        1,
        "compare_periods",
        {},
        True,
        json.dumps({"scope": scope, "groups": groups, "reconciliation": {"matched": matched}}),
        1,
    )


def test_report_and_context_survive_new_archive_instance(archive):
    value = snapshot()
    saved = run(archive.save(value, trusted=True, state={"scope": SCOPE}, revision=0))
    other = AnalysisArchive(archive.engine)
    assert saved["archive_status"] == "saved" and saved["context_applied"]
    assert run(other.report(value["trace_id"]))["created_at"]
    context = run(other.context(value["session_id"]))
    assert context["state"]["scope"] == SCOPE and context["history"][-1]["content"] == "合成结论"
    assert run(other.conversations())[0]["session_id"] == value["session_id"]


def test_failed_review_is_archived_but_not_trusted_context(archive):
    first = run(archive.save(snapshot(), trusted=True, state={"scope": SCOPE}, revision=0))
    failed = run(
        archive.save(
            snapshot(answer="未经核实", reflection_status="failed"),
            trusted=False,
            state={"bad": True},
            revision=1,
        )
    )
    context = run(archive.context("test-session"))
    assert not failed["context_applied"] and context["state"] == {"scope": SCOPE}
    assert context["history"][-1]["content"] == first["answer"]
    assert len(run(archive.reports("test-session"))) == 2


def test_old_revision_cannot_override_new_conditions(archive):
    run(archive.save(snapshot(), trusted=True, state={"region": "NORTH"}, revision=0))
    stale = run(
        archive.save(snapshot(answer="旧条件"), trusted=True, state={"region": "SOUTH"}, revision=0)
    )
    assert not stale["context_applied"] and "较早" in stale["analysis"]["warnings"][0]
    assert run(archive.context("test-session"))["state"] == {"region": "NORTH"}


def test_save_is_idempotent_and_snapshot_detached(archive):
    value = snapshot()
    run(archive.save(value, trusted=True, state={}, revision=0))
    value["answer"] = "mutation"
    saved = run(archive.save(value, trusted=True, state={}, revision=0))
    assert saved["answer"] == "合成结论" and len(run(archive.reports("test-session"))) == 1
    assert run(archive.context("test-session"))["revision"] == 1


def test_context_window_is_bounded_and_clearing_keeps_reports(archive):
    for index in range(4):
        run(
            archive.save(
                snapshot(question=str(index)), trusted=True, state={"index": index}, revision=index
            )
        )
    assert len(run(archive.context("test-session"))["history"]) == 4
    run(archive.clear_context("test-session"))
    context = run(archive.context("test-session"))
    assert context["history"] == [] and context["state"] == {} and context["revision"] == 5
    assert len(run(archive.reports("test-session"))) == 4


def test_legacy_rows_are_not_misrepresented_as_reports(archive):
    with Session(archive.engine) as session, session.begin():
        session.add(Conversation(id=str(uuid.uuid4()), title="legacy"))
    assert run(archive.conversations()) == []


def test_corrupt_context_fails_closed_before_it_enters_model(archive):
    run(archive.save(snapshot(), trusted=True, state={}, revision=0))
    with Session(archive.engine) as session, session.begin():
        row = session.get(Conversation, conversation_id("test-session"))
        row.meta = {**row.meta, "recent": [{"role": "system", "content": "untrusted"}]}
    with pytest.raises(HistoryUnavailable):
        run(archive.context("test-session"))


def test_bar_and_contribution_data_match_exact_tool_strings():
    tables, charts = build_artifacts([comparison_step()])
    assert tables[0]["rows"][0] == ["华东", "100.25", "80.1", "-20.15"]
    assert charts[0]["series"][0]["values"] == ["100.25", "50"]
    assert charts[1]["kind"] == "contribution" and charts[1]["evidence_ids"] == ["E1"]


def test_mismatched_totals_never_produce_contribution_chart():
    tables, charts = build_artifacts([comparison_step(False)])
    assert tables and [item["kind"] for item in charts] == ["bar"]


def test_rate_contribution_is_labeled_percentage_points():
    tables, charts = build_artifacts([comparison_step(rate=True)])
    assert "变化贡献（百分点）" in tables[0]["columns"]
    assert charts[1]["unit"] == "百分点"


def test_trend_sort_changes_chart_only_and_preserves_query_row_order():
    step = sql_step(["month", "sales"], [["2025-12", "80.25"], ["2025-11", "100.50"]])
    tables, charts = build_artifacts([step])
    assert tables[0]["rows"][0][0] == "2025-12"
    assert charts[0]["kind"] == "line" and charts[0]["labels"] == ["2025-11", "2025-12"]
    assert charts[0]["series"][0]["values"] == ["100.50", "80.25"]
    assert "未声明" in charts[0]["unit"]


@pytest.mark.parametrize(
    "rows,truncated",
    [
        ([["2025-11", None], ["2025-12", 2]], False),
        ([["2025-11", "9007199254740993.25"], ["2025-12", 2]], False),
        ([["2025-11", 1], ["2025-12", 2]], True),
        ([["2025-11", 1], ["2025-11", 2]], False),
    ],
)
def test_undefined_unsafe_or_partial_data_is_not_plotted(rows, truncated):
    tables, charts = build_artifacts([sql_step(["month", "value"], rows, truncated=truncated)])
    assert tables and not charts


def test_no_artifacts_are_invented_from_failed_or_non_json_tools():
    steps = [
        ToolStep(1, "run_sql", {}, False, "error", 1),
        ToolStep(2, "run_sql", {}, True, '{"bad":', 1),
    ]
    assert build_artifacts(steps) == ([], [])


def test_state_comes_from_tools_and_upload_selection_clears_old_metric():
    step = ToolStep(1, "profile_dataset", {}, True, '{"status":"warning","table":"uploads.new"}', 1)
    state = confirmed_state([step], {"scope": SCOPE, "dataset_table": "uploads.old"})
    assert state == {"dataset_table": "uploads.new"}


def test_exports_use_saved_data_and_contain_scope_review_and_evidence(archive):
    tables, charts = build_artifacts([comparison_step()])
    value = snapshot(
        tables=tables,
        charts=charts,
        steps=[{"tool": "run_sql", "arguments": {"sql": "SELECT 1"}, "output": "actual"}],
    )
    saved = run(archive.save(value, trusted=True, state={}, revision=0))
    content, mime = export_report(saved, "csv")
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["分析记录ID", "证据编号", "核验状态"]
    assert rows[1][-3:] == ["100.25", "80.1", "-20.15"]
    assert rows[1][1] == "E1" and "基期（元）" in rows[0]
    md, _ = export_report(saved, "md")
    assert "SELECT 1" in md.decode() and "actual" in md.decode()
    xlsx, _ = export_report(saved, "xlsx")
    workbook = load_workbook(io.BytesIO(xlsx))
    assert "查询证据" in workbook.sheetnames
    assert workbook["结果1"].cell(7, 2).value == "100.25"


@pytest.mark.parametrize(
    "value", ['=HYPERLINK("https://evil.invalid")', "+cmd", "-1+2", "@SUM(A1)", " \t=1"]
)
def test_csv_formula_injection_is_escaped(value):
    assert spreadsheet_text(value).startswith("'")


def test_xlsx_keeps_formula_text_and_long_content_without_silent_truncation():
    text = "x" * 40000
    value = snapshot(
        answer=text,
        tables=[
            {
                "id": "t",
                "title": "test",
                "columns": ["label", "value"],
                "rows": [["=1+2", "9007199254740993.25"]],
                "scope": {},
                "evidence_ids": ["E1"],
            }
        ],
    )
    content, _ = export_report(value, "xlsx")
    workbook = load_workbook(io.BytesIO(content), data_only=False)
    assert (
        workbook["结果1"].cell(7, 1).data_type == "s"
        and workbook["结果1"].cell(7, 1).value == "=1+2"
    )
    assert workbook["结果1"].cell(7, 2).value == "9007199254740993.25"
    combined = "".join(
        str(row[1].value or "")
        for row in workbook["分析说明"]
        if row[1].value and str(row[1].value).startswith("x")
    )
    assert combined == text
    assert spreadsheet_text("-10.25") == "-10.25"


def test_markdown_escapes_html_and_external_image_markup():
    content, _ = export_report(
        snapshot(answer="<script>bad()</script> ![x](https://evil.invalid/p.png)"), "md"
    )
    text = content.decode()
    assert "<script>" not in text and r"\!\[x\]" in text


def test_archive_context_restores_after_memory_expiry_and_injects_conditions(archive, registry):
    async def scenario():
        state = {
            "scope": {
                "source": "ecommerce_v1",
                "start": "2025-11-01",
                "end": "2025-12-01",
                "unit": "元",
                "region_code": "NORTH",
            }
        }
        await archive.save(snapshot(), trusted=True, state=state, revision=0)

        def followup(messages, kwargs):
            assert (
                "上次已确认的分析条件" in messages[0]["content"]
                and "NORTH" in messages[0]["content"]
            )
            assert any(message.get("content") == "合成结论" for message in messages)
            return reply("下一轮合成答案")

        instance = AgentOrchestrator(
            ScriptedLLM([followup]),
            registry,
            InMemorySessionStore(),
            system_prompt="SYSTEM",
            enable_reflection=False,
        )
        instance.enable_history(archive)
        result = await instance.run("再看华南", "test-session")
        assert result.archive_status == "saved"

    run(scenario())


def test_archive_read_failure_prevents_paid_model_call(archive, registry, monkeypatch):
    monkeypatch.setattr(archive, "context", AsyncMock(side_effect=HistoryUnavailable()))
    llm = ScriptedLLM([])
    instance = AgentOrchestrator(llm, registry, InMemorySessionStore(), system_prompt="SYSTEM")
    instance.enable_history(archive)
    with pytest.raises(HistoryUnavailable):
        run(instance.run("q", "s"))
    assert not llm.calls


def test_archive_write_failure_preserves_answer_without_automatic_retry(
    archive, registry, monkeypatch
):
    monkeypatch.setattr(archive, "save", AsyncMock(side_effect=HistoryUnavailable()))
    llm = ScriptedLLM([reply("已生成的合成答案")])
    instance = AgentOrchestrator(
        llm, registry, InMemorySessionStore(), system_prompt="SYSTEM", enable_reflection=False
    )
    instance.enable_history(archive)
    result = run(instance.run("q", "s"))
    assert result.answer == "已生成的合成答案" and result.archive_status == "unconfirmed"
    assert len(llm.calls) == 1


def test_orchestrator_failed_review_never_updates_structured_conditions(archive, registry):
    run(archive.save(snapshot(), trusted=True, state={"scope": SCOPE}, revision=0))
    llm = ScriptedLLM(
        [reply("", tool_call("run_sql", sql="SELECT 1 AS value")), reply("未经核实的结论")]
    )
    instance = AgentOrchestrator(llm, registry, InMemorySessionStore(), system_prompt="SYSTEM")
    instance.enable_history(archive)
    instance._reflector.reflect = AsyncMock(return_value=ReflectionReport(False, 20, issues=[]))
    result = run(instance.run("另一个问题", "test-session"))
    assert result.archive_status == "saved" and not result.context_applied
    assert run(archive.context("test-session"))["state"] == {"scope": SCOPE}
    assert len(run(archive.reports("test-session"))) == 2


def test_history_api_and_exports_do_not_requery_or_call_model(archive, registry):
    value = run(archive.save(snapshot(), trusted=True, state={"scope": SCOPE}, revision=0))
    app = create_app()
    with TestClient(app) as client:
        app.state.analysis_archive = archive
        assert client.get("/api/v1/conversations").json()[0]["session_id"] == "test-session"
        assert (
            client.get("/api/v1/conversations/test-session").json()["reports"][0]["trace_id"]
            == value["trace_id"]
        )
        assert client.get("/api/v1/analyses/" + value["trace_id"]).json()["answer"] == "合成结论"
        result = client.get("/api/v1/analyses/" + value["trace_id"] + "/export?format=md")
        assert result.status_code == 200 and "attachment" in result.headers["content-disposition"]
        assert client.get("/api/v1/analyses/not-a-uuid").status_code == 422
        assert client.get("/api/v1/analyses/" + str(uuid.uuid4())).status_code == 404


def test_http_and_sse_return_same_persisted_artifact_fields(archive, registry):
    for streaming in (False, True):
        llm = ScriptedLLM(
            [
                reply(
                    "",
                    tool_call(
                        "run_sql",
                        sql="SELECT '2025-11' AS month, 10.25 AS value UNION ALL SELECT '2025-12', 8.5",
                    ),
                ),
                reply("合成趋势"),
            ]
        )
        instance = AgentOrchestrator(
            llm, registry, InMemorySessionStore(), system_prompt="SYSTEM", enable_reflection=False
        )
        instance.enable_history(archive)
        app = create_app()
        with TestClient(app) as client:
            app.state.agent = instance
            app.state.analysis_archive = archive
            response = client.post(
                "/api/v1/chat" + ("/stream" if streaming else ""), json={"question": "合成趋势分析"}
            )
            value = (
                [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ][-1]
                if streaming
                else response.json()
            )
            assert value["archive_status"] == "saved" and value["charts"][0]["kind"] == "line"
            saved = client.get("/api/v1/analyses/" + value["trace_id"]).json()
            assert saved["tables"] == value["tables"] and saved["charts"] == value["charts"]
