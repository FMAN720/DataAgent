"""Registered metrics on a real, uniquely owned imported PostgreSQL table."""

from __future__ import annotations

import os
import uuid

import pytest

from app.analysis.workspace import WorkspaceStore
from app.analytics.semantic_tools import QueryBusinessMetricTool
from app.analytics.semantics import BusinessProfile, SemanticCatalog
from tests.conftest import run
from tests.test_phase4_postgres import env  # noqa: F401

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Opt in with RUN_POSTGRES_TESTS=1"
    ),
]


def test_registered_metric_on_imported_postgres_table(env, tmp_path):
    async def scenario():
        marker = uuid.uuid4().hex
        raw = f"external_id,day,amount,region,run_marker\n001,2025-12-01,12345,EAST,{marker}\n002,2025-12-02,20000,SOUTH,{marker}\n".encode()
        staged = await env.stage(raw, filename="owned-core-finance.csv")
        await env.service.confirm(staged["id"])
        assert await env.worker.run_once()
        ready = await env.service.get(staged["id"])
        assert ready["status"] == "ready"
        profile = BusinessProfile.model_validate(
            {
                "id": "pg_finance",
                "name": "合成财务",
                "metrics": [
                    {
                        "id": "income",
                        "name": "实收收入",
                        "table": staged["table"],
                        "primary_key": ["external_id"],
                        "date_column": "day",
                        "aggregation": "sum",
                        "storage_unit": "分",
                        "value_column": "amount",
                        "dimensions": ["region"],
                    }
                ],
            }
        )
        catalog = SemanticCatalog(WorkspaceStore(tmp_path / "workspace.sqlite3"))
        catalog.save(profile, 0)
        tool = QueryBusinessMetricTool(env.db)
        with catalog.activate(catalog.snapshot("pg_finance")):
            result = await tool.execute(
                metric_id="income", start="2025-12-01", end="2026-01-01", dimension="region"
            )
            assert result["summary"]["value"] == "323.45"
            assert {row["value"] for row in result["groups"]} == {"123.45", "200"}
            literal = await tool.execute(
                metric_id="income",
                start="2025-12-01",
                end="2026-01-01",
                filters=[{"column": "region", "values": ["EAST' OR 1=1 --"]}],
            )
            assert literal["status"] == "empty" and literal["summary"]["value"] is None

    run(scenario())
