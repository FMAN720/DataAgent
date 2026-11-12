"""Real SQL + deterministic arithmetic, including zero baselines and scope mismatch."""

import json
from decimal import Decimal

import pytest

from app.analytics.comparison import AnalysisScope, compare_results
from app.analytics.engine import QueryResult, SQLValidationError
from app.core.tools.base import ToolError
from tests.conftest import run

SCOPE = dict(
    metric="净销售额",
    source="演示业务库",
    unit="元",
    dimension="区域",
    base_start="2025-11-01",
    base_end="2025-12-01",
    current_start="2025-12-01",
    current_end="2026-01-01",
)


def result(columns, rows, truncated=False):
    return QueryResult(columns, rows, truncated, 0)


def additive(rows, totals=None, scope=None):
    return compare_results(
        AnalysisScope(**(scope or SCOPE)),
        result(["segment", "base_value", "current_value"], rows),
        result(
            ["base_value", "current_value"],
            totals
            if totals is not None
            else [[sum(row[1] for row in rows), sum(row[2] for row in rows)]],
        ),
    )


RATE_COLUMNS = ["base_numerator", "base_denominator", "current_numerator", "current_denominator"]


def rate(rows, totals=None):
    return compare_results(
        AnalysisScope(**{**SCOPE, "metric": "退款率", "metric_kind": "rate", "unit": "%"}),
        result(["segment", *RATE_COLUMNS], rows),
        result(
            RATE_COLUMNS,
            totals
            if totals is not None
            else [[sum(row[index] for row in rows) for index in range(1, 5)]],
        ),
    )


def test_additive_signed_contributions_and_offsets():
    data = additive([["华东", 100, 50], ["华南", 20, 40]])
    assert data["summary"] == {"base": "120", "current": "90", "delta": "-30", "change_pct": "-25"}
    assert Decimal(data["groups"][0]["contribution_pct"]) > 100
    assert Decimal(data["groups"][1]["contribution_pct"]) < 0
    assert sum(Decimal(row["delta"]) for row in data["groups"]) == -30
    assert not data["causal_claim_supported"]


@pytest.mark.parametrize("base,current", [(0, 10), (-5, 10), (0, 0)])
def test_nonpositive_baseline_has_no_growth_rate(base, current):
    data = additive([["新增", base, current]])
    assert data["summary"]["change_pct"] is None


def test_zero_net_change_keeps_offsets_without_contribution_percent():
    data = additive([["A", 10, 20], ["B", 20, 10]])
    assert data["summary"]["delta"] == "0"
    assert all(row["contribution_pct"] is None for row in data["groups"])


def test_scope_mismatch_never_returns_contribution():
    data = additive([["明细", 120, 150]], [[100, 110]])
    assert data["status"] == "scope_mismatch"
    assert not data["reconciliation"]["matched"]
    assert data["groups"][0]["contribution_pct"] is None
    assert data["reconciliation"]["differences"] == {"base_value": "20", "current_value": "40"}


def test_refund_rate_uses_denominator_weighting_and_exact_decomposition():
    data = rate([["A", 10, 100, 30, 100], ["B", 40, 100, 5, 1000]])
    assert data["summary"]["base"] == "25"
    assert Decimal(data["summary"]["current"]) == Decimal("3.18181818")
    contributions = sum(Decimal(row["contribution_pp"]) for row in data["groups"])
    assert abs(contributions - Decimal(data["summary"]["delta_pp"])) <= Decimal("0.00000002")
    assert any(Decimal(row["within_pp"]) != 0 for row in data["groups"])
    assert any(Decimal(row["mix_pp"]) != 0 for row in data["groups"])


def test_new_and_disappeared_groups_use_mix_convention():
    data = rate([["新增", 0, 0, 10, 100], ["消失", 40, 100, 0, 0]])
    assert all(row["within_pp"] == "0" for row in data["groups"])
    assert sum(Decimal(row["contribution_pp"]) for row in data["groups"]) == -30


def test_total_zero_denominator_stays_undefined():
    data = rate([["A", 0, 0, 1, 10]])
    assert data["summary"]["base"] is None and data["summary"]["delta_pp"] is None
    assert data["groups"][0]["contribution_pp"] is None


def test_rate_mismatch_checks_both_numerator_and_denominator():
    data = rate([["A", 1, 10, 2, 10]], [[1, 11, 2, 10]])
    assert data["status"] == "scope_mismatch"
    assert data["groups"][0]["within_pp"] is None


@pytest.mark.parametrize(
    "row",
    [
        ["A", None, 10],
        ["A", True, 10],
        ["A", float("nan"), 10],
        ["A", float("inf"), 10],
        ["A", "1", 10],
    ],
)
def test_missing_and_invalid_values_are_not_silently_zeroed(row):
    with pytest.raises(ToolError):
        additive([row], [[0, 10]])


@pytest.mark.parametrize("rows", [[["A", 1, 2], ["A", 3, 4]], []])
def test_duplicate_groups_and_empty_data_rejected(rows):
    with pytest.raises(ToolError):
        additive(rows, [[0, 0]])


def test_null_group_is_retained_distinct_from_named_group():
    data = additive([[None, 1, 2], ["未分组", 3, 4]])
    assert len(data["groups"]) == 2 and any(row["segment"] is None for row in data["groups"])


def test_long_results_aggregate_other_groups_without_losing_change():
    data = additive([[str(i), 100, 100 + i] for i in range(30)])
    assert data["group_count"] == 30 and len(data["groups"]) == 9
    assert data["groups"][-1]["is_other"]
    assert sum(Decimal(row["delta"]) for row in data["groups"]) == 435
    assert len(json.dumps(data, ensure_ascii=False)) < 8000


def test_rate_table_and_maximum_scope_preserve_valid_json_under_tool_budget():
    scope = AnalysisScope(
        **{
            **SCOPE,
            "metric": "测" * 80,
            "metric_kind": "rate",
            "unit": "%",
            "source": "源" * 120,
            "dimension": "维" * 80,
            "assumptions": ["假" * 160] * 3,
            "filters": ["筛" * 160] * 4,
        }
    )
    rows = [[str(i).zfill(128), 10**20, 10**21, 2 * 10**20, 10**21] for i in range(30)]
    data = compare_results(
        scope,
        result(["segment", *RATE_COLUMNS], rows),
        result(RATE_COLUMNS, [[sum(row[index] for row in rows) for index in range(1, 5)]]),
    )
    assert len(json.dumps(data, ensure_ascii=False)) < 8000


@pytest.mark.parametrize(
    "fields",
    [
        dict(current_start="2025-11-15"),
        dict(base_end="2025-11-01"),
        dict(base_start="2025-02-30"),
        dict(base_start="20251101"),
        dict(metric_kind="rate", unit="元"),
    ],
)
def test_invalid_or_overlapping_periods_and_rate_unit_rejected(fields):
    with pytest.raises(ValueError):
        AnalysisScope(**{**SCOPE, **fields})


def test_prepare_scope_does_not_invent_missing_conditions(registry):
    data = json.loads(
        run(registry.invoke("prepare_analysis", {"scope": {"metric": "销售额"}})).content
    )
    assert data["status"] == "needs_clarification" and len(data["questions"]) == 5
    assert data["scope"]["current_start"] == ""


def test_comparison_rejects_truncated_query_before_calculation():
    with pytest.raises(ToolError, match="截断"):
        compare_results(
            AnalysisScope(**SCOPE),
            result(["segment", "base_value", "current_value"], [["A", 1, 2]], True),
            result(["base_value", "current_value"], [[1, 2]]),
        )


def test_comparison_executes_real_sql_and_preserves_decimal_amounts(registry):
    data = run(
        registry.invoke(
            "compare_periods",
            {
                "scope": SCOPE,
                "detail_sql": "SELECT '华东' AS segment, 10.25 AS base_value, 12.50 AS current_value",
                "total_sql": "SELECT 10.25 AS base_value, 12.50 AS current_value",
            },
        )
    )
    assert data.ok
    assert json.loads(data.content)["summary"]["delta"] == "2.25"


def test_comparison_uses_existing_readonly_guards_and_missing_alias_errors(registry, db):
    with pytest.raises(SQLValidationError):
        run(db.execute_many(["SELECT 1", "DELETE FROM orders"]))
    with pytest.raises(SQLValidationError):
        run(db.execute_many(["SELECT 1; SELECT 2"]))
    response = run(
        registry.invoke(
            "compare_periods", {"scope": SCOPE, "detail_sql": "SELECT 1", "total_sql": "SELECT 1"}
        )
    )
    assert not response.ok and "segment" in response.content
