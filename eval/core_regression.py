"""Offline scenario regression of real tools + orchestration; no live-model accuracy claim.

The fixture model submits the declared tool request and fixed oracle answer. This verifies
execution, numerical gates and persistence, not a real model's tool selection or reasoning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.analysis.workspace import WorkspaceStore
from app.analytics.engine import AnalyticsDatabase
from app.analytics.semantic_tools import GetBusinessDefinitionTool, QueryBusinessMetricTool
from app.analytics.semantics import SemanticCatalog
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.infrastructure.llm.model_router import LLMResponse, ToolCall
from eval.core_fixtures import build_core_database, domain_profile

CASES_PATH = Path(__file__).with_name("core_cases.json")


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


class FixtureModel:
    def __init__(self, case: dict):
        self.case, self.calls = case, 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            arguments = {
                "metric_id": self.case["metric_id"],
                "start": self.case.get("start", "2025-12-01"),
                "end": self.case.get("end", "2026-01-01"),
            }
            if "filters" in self.case:
                arguments["filters"] = self.case["filters"]
            return LLMResponse(
                content="",
                model_id="offline-fixture",
                tool_calls=[
                    ToolCall(
                        id="fixture-query",
                        name="query_business_metric",
                        arguments=json.dumps(arguments, ensure_ascii=False),
                    )
                ],
            )
        if self.calls == 2:
            return LLMResponse(
                content=self.case.get("answer", "当前数据检查未通过。"), model_id="offline-fixture"
            )
        raise AssertionError("Unexpected extra fixture-model request")


async def run_case(case: dict, database_path: Path, workspace_path: Path) -> dict:
    store = WorkspaceStore(workspace_path)
    catalog = SemanticCatalog(store)
    profile = domain_profile(case["domain"], case.get("table"))
    catalog.save(profile, 0)
    database = AnalyticsDatabase(f"sqlite:///{database_path.as_posix()}")
    registry = ToolRegistry()
    registry.register(GetBusinessDefinitionTool())
    registry.register(QueryBusinessMetricTool(database))
    model = FixtureModel(case)
    agent = AgentOrchestrator(
        model,
        registry,
        InMemorySessionStore(),
        system_prompt="Offline scenario regression",
        enable_reflection=False,
        workspace=store,
        semantic_catalog=catalog,
    )
    try:
        response = await agent.run(case["question"], business_profile_id=profile.id)
        step = response.steps[0]
        payload = json.loads(step.output)
        checks = {"tool_ok": step.ok == case.get("tool_ok", True)}
        if "expected_value" in case:
            checks["value"] = payload.get("summary", {}).get("value") == case["expected_value"]
        if "expected_status" in case:
            checks["tool_status"] = payload.get("status") == case["expected_status"]
        if "validation" in case:
            checks["validation"] = response.validation["status"] == case["validation"]
        checks["clarification"] = response.needs_clarification == case.get("clarification", False)
        checks["success"] = response.success == case.get("success", True)
        assert response.task_id is not None
        task = store.get_task(response.task_id)
        checks["persistent_task"] = task["status"] == (
            "completed" if response.success else "failed"
        )
        checks["profile_bound"] = (
            response.business_profile["digest"] == catalog.snapshot(profile.id).reference["digest"]
        )
        return {
            "id": case["id"],
            "domain": case["domain"],
            "passed": all(checks.values()),
            "checks": checks,
            "actual_value": payload.get("summary", {}).get("value"),
            "validation": response.validation["status"],
            "fixture_calls": model.calls,
            "duration_ms": response.duration_ms,
        }
    finally:
        await agent.aclose()
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=Path("reports/core-analysis-upgrade/offline-regression.json")
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent-core-regression-") as directory:
        root = Path(directory)
        database_path = root / "synthetic.db"
        build_core_database(database_path)
        results = [
            asyncio.run(run_case(case, database_path, root / case["id"] / "workspace.sqlite3"))
            for case in load_cases()
        ]
    report = {
        "kind": "offline_tool_and_workflow_regression",
        "created_at": datetime.now(UTC).isoformat(),
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "provider_requests": 0,
        "limits": "Synthetic fixtures and scripted model; not live-model accuracy, causal validity or provider interoperability.",
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Offline core regression: {report['passed']}/{report['total']}; provider requests: 0")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
