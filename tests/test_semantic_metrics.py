from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
from pydantic import ValidationError

from app.analysis.validation import validate_answer
from app.analysis.workspace import WorkspaceConflict, WorkspaceStore
from app.analytics.engine import AnalyticsDatabase
from app.analytics.semantic_tools import QueryBusinessMetricTool
from app.analytics.semantics import ACTIVE_PROFILE, BusinessProfile, SemanticCatalog
from app.core.agent.react_agent import ToolStep
from app.core.tools.base import ToolError
from tests.conftest import run


def finance_profile(table="receipts"):
    return BusinessProfile.model_validate(
        {
            "id": "finance",
            "name": "财务收款",
            "metrics": [
                {
                    "id": "income",
                    "name": "实收收入",
                    "table": table,
                    "primary_key": ["receipt_id"],
                    "date_column": "day",
                    "aggregation": "sum",
                    "value_column": "amount_cents",
                    "storage_unit": "分",
                    "dimensions": ["region"],
                    "filter_columns": ["status"],
                    "filters": [{"column": "status", "operator": "eq", "values": ["confirmed"]}],
                }
            ],
        }
    )


@pytest.fixture()
def semantic_env(tmp_path):
    path = tmp_path / "finance.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE receipts(receipt_id INTEGER, day TEXT, amount_cents INTEGER, region TEXT, status TEXT);
            INSERT INTO receipts VALUES
              (1,'2025-12-01',12345,'华东','confirmed'),
              (2,'2025-12-02',20000,'华南','confirmed'),
              (3,'2025-12-03',99999,'华东','draft'),
              (4,'2025-11-01',100,'华东','confirmed');
            CREATE TABLE support(id INTEGER,day TEXT,resolved INTEGER,total INTEGER,team TEXT);
            INSERT INTO support VALUES (1,'2025-12-01',1,10,'A'),(2,'2025-12-02',90,100,'B');
        """)
    store = WorkspaceStore(tmp_path / "local" / "workspace.sqlite3")
    catalog = SemanticCatalog(store)
    catalog.save(finance_profile(), 0)
    db = AnalyticsDatabase(f"sqlite:///{path.as_posix()}", max_rows=30)
    yield catalog, QueryBusinessMetricTool(db), path
    db.dispose()


def query(catalog, tool, **kwargs):
    with catalog.activate(catalog.snapshot(kwargs.pop("profile_id", "finance"))):
        return run(
            tool.execute(
                metric_id=kwargs.pop("metric_id", "income"),
                start="2025-12-01",
                end="2026-01-01",
                **kwargs,
            )
        )


def test_registered_finance_metric_has_units_source_and_cell_provenance(semantic_env):
    catalog, tool, _ = semantic_env
    payload = query(catalog, tool, dimension="region")
    assert payload["summary"] == {"value": "323.45", "record_count": 2}
    assert payload["groups"][0] == {"segment": "华南", "value": "200"}
    assert payload["profile_ref"]["revision"] == 1
    step = ToolStep(1, tool.name, {}, True, json.dumps(payload, ensure_ascii=False), 0)
    assert validate_answer("实收收入：323.45 元。", [step]).status == "passed"
    assert validate_answer("实收收入：32,345 元。", [step]).status == "failed"
    assert "323.45" not in payload["query"]


def test_ratio_uses_total_numerator_and_denominator(semantic_env):
    catalog, tool, _ = semantic_env
    profile = BusinessProfile.model_validate(
        {
            "id": "support",
            "name": "客服",
            "metrics": [
                {
                    "id": "resolved",
                    "name": "解决率",
                    "table": "support",
                    "primary_key": ["id"],
                    "date_column": "day",
                    "aggregation": "ratio",
                    "storage_unit": "比例",
                    "numerator_column": "resolved",
                    "denominator_column": "total",
                    "dimensions": ["team"],
                }
            ],
        }
    )
    catalog.save(profile, 0)
    payload = query(catalog, tool, profile_id="support", metric_id="resolved", dimension="team")
    assert payload["summary"]["value"] == "82.72727273"
    assert payload["metric"]["unit"] == "%"
    assert {group["value"] for group in payload["groups"]} == {"10", "90"}


def test_filter_values_are_bound_not_sql_code(semantic_env):
    catalog, tool, path = semantic_env
    injection = "华东' OR 1=1 --"
    payload = query(catalog, tool, filters=[{"column": "region", "values": [injection]}])
    assert payload["status"] == "empty"
    assert payload["summary"]["value"] is None
    assert injection not in payload["query"]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 4


def test_empty_and_real_zero_remain_distinct(semantic_env):
    catalog, tool, path = semantic_env
    empty = query(catalog, tool, filters=[{"column": "region", "values": ["无此区域"]}])
    assert empty["summary"]["value"] is None and empty["status"] == "empty"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO receipts VALUES(5,'2025-12-05',0,'零值区域','confirmed')")
    actual_zero = query(catalog, tool, filters=[{"column": "region", "values": ["零值区域"]}])
    assert actual_zero["status"] == "ready" and actual_zero["summary"]["value"] == "0"


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO receipts VALUES(1,'2025-12-04',10,'华东','confirmed')",
        "UPDATE receipts SET amount_cents=NULL WHERE receipt_id=1",
        "UPDATE receipts SET amount_cents='not numeric' WHERE receipt_id=1",
        "UPDATE receipts SET day='2025-02-30' WHERE receipt_id=1",
    ],
)
def test_quality_gate_blocks_invalid_data(semantic_env, mutation):
    catalog, tool, path = semantic_env
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(mutation)
    payload = query(catalog, tool)
    assert payload["status"] == "needs_clarification"
    assert payload["result"] is None and "summary" not in payload


def test_unregistered_columns_and_missing_table_do_not_fall_back(semantic_env):
    catalog, tool, _ = semantic_env
    with pytest.raises(ToolError, match="维度"):
        query(catalog, tool, dimension="receipt_id")
    with pytest.raises(ToolError, match="筛选"):
        query(catalog, tool, filters=[{"column": "amount_cents", "values": [1]}])
    catalog.save(finance_profile("missing_table"), 1)
    with pytest.raises(ToolError, match="已不可用"):
        query(catalog, tool)


def test_revisions_are_immutable_and_compare_and_swap(semantic_env):
    catalog, _tool, _ = semantic_env
    original = catalog.snapshot("finance")
    modified = finance_profile()
    modified.name = "更新后的财务口径"
    saved = catalog.save(modified, 1)
    assert saved["revision"] == 2 and catalog.snapshot("finance").reference != original.reference
    assert catalog.store.get_profile("finance", 1)["definition"]["name"] == "财务收款"
    with pytest.raises(WorkspaceConflict):
        catalog.save(modified, 1)


def test_context_snapshots_are_isolated_and_reset(semantic_env):
    catalog, _tool, _ = semantic_env

    async def task(identifier):
        with catalog.activate(catalog.snapshot(identifier)):
            await asyncio.sleep(0)
            return ACTIVE_PROFILE.get().reference["id"]

    async def concurrent():
        return await asyncio.gather(task("finance"), task("ecommerce_v1"))

    assert run(concurrent()) == ["finance", "ecommerce_v1"]
    assert ACTIVE_PROFILE.get() is None


def test_arbitrary_sql_and_averaged_rates_are_not_profile_definitions():
    data = finance_profile().model_dump()
    data["metrics"][0]["sql"] = "SELECT 1"
    with pytest.raises(ValidationError):
        BusinessProfile.model_validate(data)
    data["metrics"][0].pop("sql")
    data["metrics"][0].update(aggregation="average", storage_unit="%")
    with pytest.raises(ValidationError):
        BusinessProfile.model_validate(data)
