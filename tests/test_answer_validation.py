from __future__ import annotations

import json

import pytest

from app.analysis.validation import validate_answer
from app.core.agent.react_agent import ToolStep


def metric(value="234927.75", unit="元", label="净销售额", *, ok=True):
    return ToolStep(
        1,
        "query_metric",
        {},
        ok,
        json.dumps(
            {
                "status": "ready",
                "metric": {"name": label, "unit": unit},
                "summary": {"value": value},
                "groups": [],
                "period": {"start": "2025-12-01", "end": "2026-01-01"},
            },
            ensure_ascii=False,
        ),
        0,
    )


def test_money_provenance_is_linked_to_actual_result_cell():
    report = validate_answer("净销售额：234,927.75 元。", [metric()])
    assert report.status == "passed"
    assert report.checks[0]["source_paths"] == ["E1.summary.value"]


@pytest.mark.parametrize("answer", ["净销售额：23,492,775.00 元。", "净销售额：2,349.28 元。"])
def test_hundredfold_unit_errors_are_blocked(answer):
    assert validate_answer(answer, [metric()]).status == "failed"


def test_declared_display_scale_and_rounding():
    assert validate_answer("净销售额：23.49 万元。", [metric()]).status == "passed"
    assert validate_answer("退款率：8.0%。", [metric("8.034", "%", "退款率")]).status == "passed"


def test_missing_rate_is_never_certified_as_zero():
    report = validate_answer("退款率：0.0%。", [metric(None, "%", "退款率")])
    assert report.status == "failed"


def test_other_metric_cannot_rescue_a_wrong_label():
    report = validate_answer("净销售额：100 元。", [metric(), metric("100", "元", "退款金额")])
    assert report.status == "failed"
    report = validate_answer(
        "净销售额：100 元，退款金额：100 元。", [metric(), metric("100", "元", "退款金额")]
    )
    assert report.status == "failed"


def test_wrong_unit_and_unlabelled_numbers_are_not_certified():
    assert validate_answer("退款率：8 元。", [metric("8", "%", "退款率")]).status == "failed"
    assert validate_answer("收入有 100 元。", [metric("100", "元", "其他金额")]).status == "partial"


def test_sql_alias_is_not_proof_of_actual_business_unit():
    step = ToolStep(
        1, "run_sql", {}, True, json.dumps({"columns": ["收入（元）"], "rows": [[125]]}), 0
    )
    report = validate_answer("收入：125 元。", [step])
    assert report.status == "partial"
    assert report.checked_claims == 0
    assert report.checks[0]["source_paths"] == ["E1.rows[0][0]"]


def test_truncated_failed_and_malformed_outputs_are_not_evidence():
    failed = metric(ok=False)
    truncated = ToolStep(
        1,
        "run_sql",
        {},
        True,
        json.dumps({"columns": ["金额（元）"], "rows": [[1]], "truncated": True}),
        0,
    )
    malformed = ToolStep(1, "query_metric", {}, True, "{broken", 0)
    report = validate_answer("金额为 1 元。", [failed, truncated, malformed])
    assert report.status == "partial" and not report.checked_claims


def test_period_checks_accept_exclusive_or_inclusive_display_end():
    for end in ["2025-12-31", "2026-01-01"]:
        assert not validate_answer(f"统计区间：2025-12-01 至 {end}", [metric()]).issues
    assert validate_answer("统计区间：2025-11-01 至 2025-11-30", [metric()]).issues
    assert not validate_answer("活动时间：2025-11-01 至 2025-11-30", [metric()]).issues


def test_dates_minutes_and_clarification_are_not_numeric_certification():
    assert validate_answer("2025 年 12 月，耗时 1 分钟。", [metric()]).status == "not_applicable"
    assert (
        validate_answer("请确认是否为 100 元？", [metric()], needs_clarification=True).status
        == "not_applicable"
    )
