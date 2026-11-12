"""真实 PostgreSQL 的固定指标、体检、日期校验与一致快照；只读、不调用模型。"""

import json
import os

import pytest

from app.analytics import AnalyticsDatabase
from app.analytics.metrics import QueryMetricTool, _invalid_timestamp
from app.analytics.quality import ProfileDatasetTool
from app.infrastructure.database.manage import load_config
from tests.conftest import run

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


@pytest.fixture
def reader():
    database = AnalyticsDatabase(load_config()["ANALYTICS_DB_URL"])
    yield database
    database.dispose()


def test_registered_sales_preserves_existing_gold_result_in_postgres(reader):
    result = run(
        QueryMetricTool(reader).execute(
            metric_id="net_sales", source="ecommerce_v1", start="2025-11-01", end="2025-12-01"
        )
    )
    assert result["status"] == "ready" and result["summary"]["value"] == "1047258.23"
    assert all('"business"."orders"' in query for query in result["queries"])


@pytest.mark.parametrize("metric_id", ["net_sales", "order_count", "refund_rate"])
def test_postgres_controlled_comparison_reconciles_all_dimensions(reader, metric_id):
    for dimension in ("overall", "region", "order_channel"):
        result = run(
            QueryMetricTool(reader).execute(
                metric_id=metric_id,
                source="ecommerce_v1",
                base_start="2025-11-01",
                base_end="2025-12-01",
                start="2025-12-01",
                end="2026-01-01",
                dimension=dimension,
            )
        )
        assert result["status"] == "ready"
        assert result["comparison"]["reconciliation"]["matched"]
        assert result["quality"]["duplicate_users"] == 0
        assert len(json.dumps(result, ensure_ascii=False)) < 8000


def test_postgres_profile_is_full_table_aggregate_and_keeps_original_cents(reader):
    result = run(
        ProfileDatasetTool(reader).execute(
            table="business.orders",
            columns=["order_id", "pay_amount"],
            key_columns=["order_id"],
            date_column="order_time",
        )
    )
    assert result["status"] == "warning"  # 日期完整性仍未验证。
    assert result["row_count"] > 9000 and result["duplicate_excess_rows"] == 0
    assert result["columns"][1]["min"] == 0 and result["columns"][1]["invalid_numeric_count"] == 0
    assert result["date_range"]["completeness"] == "unverified"


def test_postgres_date_validation_handles_dirty_text_without_cast_failure(reader):
    values = [
        "bad",
        "2025-02-30 12:00:00",
        "2025-02-29 12:00:00",
        "2024-02-29 12:00:00",
        "2025-04-31 12:00:00",
        "2025-01-01 24:00:00",
        "0000-01-01 12:00:00",
        "2025-12-31 23:59:59",
    ]
    rows = ",".join("('" + value + "')" for value in values)
    result = run(
        reader.execute(
            f"SELECT CASE WHEN {_invalid_timestamp('postgresql')} THEN 1 ELSE 0 END AS invalid FROM (VALUES {rows}) o(order_time)"
        )
    )
    assert result.rows == [[1], [1], [1], [0], [1], [1], [1], [0]]


def test_four_queries_and_preflight_share_readonly_repeatable_snapshot(reader):
    sql = "SELECT txid_current_snapshot()::text, current_setting('transaction_isolation'), current_setting('transaction_read_only')"
    checks = []
    results = run(
        reader.execute_snapshot([sql] * 4, validate_first=lambda first: checks.append(first.rows))
    )
    assert all(result.rows == results[0].rows for result in results)
    assert results[0].rows[0][1:] == ["repeatable read", "on"]
    assert checks == [results[0].rows]
