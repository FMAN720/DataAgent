"""Read-only PostgreSQL snapshot and business-scope reconciliation; no external API."""

import json
import os

import pytest

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.core.tools.registry import ToolRegistry
from app.infrastructure.database.manage import load_config
from tests.conftest import GLOSSARY, run
from tests.test_phase6_comparison import SCOPE

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


@pytest.fixture
def reader():
    database = AnalyticsDatabase(load_config()["ANALYTICS_DB_URL"], max_rows=50)
    yield database
    database.dispose()


def test_postgres_comparison_queries_share_repeatable_read_snapshot(reader):
    query = "SELECT txid_current_snapshot()::text AS snapshot, current_setting('transaction_isolation') AS isolation, current_setting('transaction_read_only') AS readonly"
    results = run(reader.execute_many([query, query]))
    assert results[0].rows == results[1].rows
    assert results[0].rows[0][1:] == ["repeatable read", "on"]


def test_real_category_detail_cannot_be_substituted_for_net_sales(reader):
    registry = ToolRegistry()
    for tool in build_analytics_tools(reader, GlossaryStore.from_file(GLOSSARY)):
        registry.register(tool)
    filters = "o.status IN ('paid','completed') AND u.is_test=0 AND o.order_time >= '2025-11-01' AND o.order_time < '2026-01-01'"
    details = f"""SELECT c.category_name AS segment,
        SUM(CASE WHEN o.order_time < '2025-12-01' THEN i.item_amount ELSE 0 END) / 100.0 AS base_value,
        SUM(CASE WHEN o.order_time >= '2025-12-01' THEN i.item_amount ELSE 0 END) / 100.0 AS current_value
        FROM orders o JOIN users u ON u.user_id=o.user_id JOIN order_items i ON i.order_id=o.order_id
        JOIN products p ON p.product_id=i.product_id JOIN categories c ON c.category_id=p.category_id
        WHERE {filters} GROUP BY c.category_name"""
    totals = f"""SELECT SUM(CASE WHEN o.order_time < '2025-12-01' THEN o.pay_amount ELSE 0 END) / 100.0 AS base_value,
        SUM(CASE WHEN o.order_time >= '2025-12-01' THEN o.pay_amount ELSE 0 END) / 100.0 AS current_value
        FROM orders o JOIN users u ON u.user_id=o.user_id WHERE {filters}"""
    outcome = run(
        registry.invoke(
            "compare_periods",
            {"scope": {**SCOPE, "dimension": "品类"}, "detail_sql": details, "total_sql": totals},
        )
    )
    assert outcome.ok
    data = json.loads(outcome.content)
    assert data["status"] == "scope_mismatch"
    assert all(row["contribution_pct"] is None for row in data["groups"])
