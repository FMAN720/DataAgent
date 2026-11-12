"""保存第一阶段修改前后的可比评测；仅使用已配置的聊天 API。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import platform
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.main import build_agent, configure_logging
from app.models.enums import AgentMode
from eval.run_eval import run_case

# 允许用 Git 原始源码快照重放同一评测，不覆盖当前工作目录中的任何源码。
ROOT = Path(inspect.getfile(build_agent)).resolve().parents[1]


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=["before", "after"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--case-timeout", type=float, default=240)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging("ERROR")
    cases_path = ROOT / "eval" / "questions.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    source_paths = [
        "app/core/agent/react_agent.py",
        "app/core/agent/reflection.py",
        "app/core/agent/orchestrator.py",
        "app/analytics/prompts.py",
        "app/analytics/engine.py",
        "eval/run_eval.py",
    ]
    report = {
        "label": args.label,
        "created_at": datetime.now(UTC).isoformat(),
        "model": settings.openai_model,
        "temperature": settings.agent_temperature,
        "mode": AgentMode.REACT.value,
        "reflection": settings.enable_reflection,
        "max_steps": settings.agent_max_steps,
        "reference_date": settings.analytics_reference_date,
        "concurrency": args.concurrency,
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "openai",
                "fastapi",
                "pydantic",
                "sqlalchemy",
                "pytest",
            )
        },
        "fingerprints": {p: fingerprint(ROOT / p) for p in source_paths},
        "questions_sha256": fingerprint(cases_path),
        "glossary_sha256": fingerprint(Path(settings.glossary_path)),
        "accounting_scope": "ReAct execution and revision only; excludes planner/reflection",
        "grading_scope": "legacy numeric/text matching; not semantic correctness",
        "trace_capture": True,
        "results": [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    agent = build_agent(settings)
    if settings.analytics_db_url.startswith("sqlite:///"):
        report["database_sha256"] = fingerprint(
            Path(settings.analytics_db_url[len("sqlite:///") :])
        )
    save()
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(case):
        async with sem:

            class RecordingAgent:
                response = None

                async def run(self, *args, **kwargs):
                    self.response = await agent.run(*args, **kwargs)
                    return self.response

            recorder = RecordingAgent()
            try:
                result = await asyncio.wait_for(
                    run_case(recorder, case, settings.analytics_db_url, AgentMode.REACT),
                    timeout=args.case_timeout,
                )
                item = asdict(result)
                print(
                    f"{case['id']}: {'PASS' if result.passed else 'FAIL'} ({result.seconds:.1f}s)",
                    flush=True,
                )
            except Exception as exc:
                # 不记录请求头、密钥或完整配置；单题失败仍然保留其他题结果。
                item = {"id": case["id"], "passed": False, "error_type": type(exc).__name__}
                print(f"{case['id']}: ERROR {type(exc).__name__}", flush=True)
            response = recorder.response
            if response is not None:
                record = agent.tracer.get_trace(response.trace_id)
                item["trace_id"] = response.trace_id
                item["steps"] = [step.to_dict() for step in response.steps]
                item["plan"] = response.plan
                item["reflection"] = response.reflection.to_dict() if response.reflection else None
                item["trace"] = [asdict(span) for span in record.spans] if record else []
            report["results"].append(item)
            save()

    await asyncio.gather(*(guarded(case) for case in cases))
    close = getattr(agent, "aclose", None)
    if close is not None:
        await close()
    by_id = {item["id"]: item for item in report["results"]}
    report["results"] = [by_id[case["id"]] for case in cases]
    passed = sum(item["passed"] for item in report["results"])
    completed = [item for item in report["results"] if "seconds" in item]
    report["summary"] = {
        "passed": passed,
        "total": len(cases),
        "accuracy": passed / len(cases) if cases else None,
        "mean_seconds": sum(item["seconds"] for item in completed) / len(completed)
        if completed
        else None,
        "mean_react_tokens": sum(item["tokens"] for item in completed) / len(completed)
        if completed
        else None,
    }
    save()
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
