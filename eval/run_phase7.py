"""Reproducible synthetic Agent evaluation. Defaults to preparation without network.

Live execution requires separate human authorization and an explicit CLI flag.
Phase 7 was implemented and validated offline at the user's request.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from loguru import logger

from app.config import Settings
from app.infrastructure.llm.model_router import ModelConfig, ModelRouter
from app.knowledge.embedding import APIEmbedder
from app.models.enums import AgentMode
from eval.phase7_budget import BudgetedTransport, EvaluationLedger, replace_with_retry
from eval.phase7_cases import ABLATION_IDS, CASES
from eval.phase7_fixtures import ROOT, DocumentFixture, build_evaluation_agent, prepare
from eval.phase7_grader import grade


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    replace_with_retry(temporary, path)


def source_fingerprint():
    files = sorted(
        [
            *ROOT.joinpath("app").rglob("*.py"),
            *ROOT.joinpath("eval").glob("phase7_*.py"),
            Path(__file__),
        ]
    )
    material = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


@contextmanager
def exclusive_run(out):
    """OS-owned lock prevents two processes from spending the same ledger budget."""
    out.mkdir(parents=True, exist_ok=True)
    with (out / "evaluation.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another evaluation process owns this output directory") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def regrade(out, manifest):
    """用当前判分规则重新评分已保存的回答，不调用任何 API。"""
    documents = {
        item["id"]: str(
            uuid.uuid5(uuid.NAMESPACE_URL, "phase7:" + manifest["fingerprint"] + ":" + item["id"])
        )
        for item in manifest["documents"]
    }
    cases = {case["id"]: case for case in CASES}
    for path in sorted((out / "results").glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        row["grade"] = grade(
            cases[row["id"]], manifest["gold"].get(row["id"]), row["response"], documents
        )
        write_json(path, row)
    return summarize(out)


def summarize(out):
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((out / "results").glob("*.json"))
    ]
    variants = {}
    for name in ("baseline", "keyword", "no_planner", "no_reflection"):
        selected = [row for row in rows if row["variant"] == name]
        if not selected:
            continue
        elapsed = sorted(row["response"]["duration_ms"] for row in selected)
        groups = {}
        for split in ("development", "holdout"):
            items = [row for row in selected if row["split"] == split]
            groups[split] = {
                "completed": len(items),
                "rule_passed": sum(row["grade"]["passed"] for row in items),
            }
        variants[name] = {
            "completed": len(selected),
            "rule_passed": sum(row["grade"]["passed"] for row in selected),
            "splits": groups,
            "p50_ms": statistics.median(elapsed),
            "p95_ms": elapsed[math.ceil(len(elapsed) * 0.95) - 1],
            "llm_calls": sum(row["response"]["llm_calls"] for row in selected),
            "chat_tokens": sum(row["response"]["usage"].get("total_tokens", 0) for row in selected),
            "citation_error_cases": sum(
                bool(row["response"].get("citation_errors")) for row in selected
            ),
            "reviewed_cases": sum(
                row["response"].get("reflection_status") == "passed" for row in selected
            ),
            "revised_cases": sum(bool(row["response"].get("revised")) for row in selected),
            "complete_usage_cases": sum(
                bool(row["response"].get("usage_breakdown", {}).get("usage_complete"))
                for row in selected
            ),
            "failures": [
                {"id": row["id"], "checks": row["grade"]["failed_checks"]}
                for row in selected
                if not row["grade"]["passed"]
            ],
        }
    paired = {}
    baseline = {row["id"]: row for row in rows if row["variant"] == "baseline"}
    for name in ("keyword", "no_planner", "no_reflection"):
        pairs = [
            (baseline[row["id"]], row)
            for row in rows
            if row["variant"] == name and row["id"] in baseline
        ]
        paired[name] = {
            "pairs": len(pairs),
            "baseline_passed": sum(a["grade"]["passed"] for a, b in pairs),
            "variant_passed": sum(b["grade"]["passed"] for a, b in pairs),
        }
    report = {
        "live_status": "not_run" if not rows else ("complete" if len(rows) == 80 else "partial"),
        "expected_runs": 80,
        "completed_runs": len(rows),
        "variants": variants,
        "paired_ablations": paired,
        "interpretation": "Rule-based acceptance only; a single synthetic run does not establish general accuracy or statistical significance.",
    }
    write_json(out / "summary.json", report)
    return report


class BoundedLLM:
    def __init__(self, router):
        self.router, self.served_models = router, set()

    async def chat(self, messages, **kwargs):
        kwargs["max_tokens"] = 2048
        response = await self.router.chat(messages, **kwargs)
        if response.raw and response.raw.get("model"):
            self.served_models.add(response.raw["model"])
        return response


async def live(out, manifest, database, limit=None):
    settings = Settings()
    if (
        settings.openai_api_base.rstrip("/") != "https://api.deepseek.com/v1"
        or settings.openai_model != "deepseek-chat"
        or settings.embedding_api_base.rstrip("/") != "https://api.siliconflow.cn/v1"
        or settings.embedding_model != "BAAI/bge-m3"
        or settings.embedding_dimensions != 1024
    ):
        raise ValueError("Provider/model differs from the reviewable evaluation scope")
    if not settings.openai_api_key or not settings.embedding_api_key:
        raise ValueError("API configuration incomplete")
    settings = settings.model_copy(
        update={
            "embedding_enabled": True,
            "embedding_revision": "phase7-" + manifest["fingerprint"],
            "embedding_max_retries": 0,
        }
    )
    lock = {
        "fixture_fingerprint": manifest["fingerprint"],
        "source_fingerprint": source_fingerprint(),
        "chat_model": settings.openai_model,
        "embedding_model": settings.embedding_model,
        "retrieval_settings": {
            key: getattr(settings, key)
            for key in (
                "embedding_dimensions",
                "embedding_revision",
                "rag_min_similarity",
                "rag_bge_m3_min_similarity",
                "rag_top_k",
                "rag_context_max_bytes",
                "rag_hybrid_require_vector_match",
            )
            if hasattr(settings, key)
        },
    }
    lock_path = out / "run-lock.json"
    if lock_path.exists() and json.loads(lock_path.read_text(encoding="utf-8")) != lock:
        raise ValueError(
            "Frozen implementation differs; preserve previous results, do not tune holdout in place"
        )
    write_json(lock_path, lock)
    ledger = EvaluationLedger(out / "request-ledger.json", manifest["fingerprint"])
    secrets = (settings.openai_api_key, settings.embedding_api_key)
    chat_transport = BudgetedTransport(
        httpx.AsyncHTTPTransport(retries=0), ledger, "chat", settings.openai_model, secrets=secrets
    )
    embedding_transport = BudgetedTransport(
        httpx.AsyncHTTPTransport(retries=0),
        ledger,
        "embedding",
        settings.embedding_model,
        secrets=secrets,
    )
    router = ModelRouter(
        [ModelConfig(settings.openai_model, settings.openai_api_key, settings.openai_api_base)],
        transport=chat_transport,
        max_retries=0,
        timeout_seconds=60,
    )
    llm = BoundedLLM(router)
    embedder = APIEmbedder(settings, transport=embedding_transport)
    documents = DocumentFixture(settings, embedder, manifest["fingerprint"])
    started = datetime.now(UTC).isoformat()
    try:
        await documents.seed()
        # Baseline is frozen before any holdout response is observed. No in-run tuning.
        schedule = [("baseline", case) for case in CASES if case["split"] == "development"]
        schedule += [("baseline", case) for case in CASES if case["split"] == "holdout"]
        schedule += [
            (variant, case)
            for variant in ("keyword", "no_planner", "no_reflection")
            for case in CASES
            if case["id"] in ABLATION_IDS
        ]
        executed = 0
        for variant, case in schedule:
            target = out / "results" / f"{variant}-{case['id']}.json"
            if target.exists():
                continue  # Resume uses the same persistent budget, never resets it.
            if limit is not None and executed >= limit:
                break  # 试跑：先跑少量题核对用量，之后按原顺序继续
            executed += 1
            agent = build_evaluation_agent(llm, database, manifest, documents, variant)
            start_request = len(ledger.data["requests"])
            try:
                mode = AgentMode.REACT if variant == "no_planner" else AgentMode.PLAN_EXECUTE
                response = await agent.run(case["question"], mode=mode)
                result = asdict(response)
                result["mode"] = response.mode.value
                result["reflection_status"] = response.reflection_status.value
                result["citations"] = [item.model_dump(mode="json") for item in response.citations]
                result["steps"] = [step.to_dict() for step in response.steps]
                row = {
                    "id": case["id"],
                    "category": case["category"],
                    "split": case["split"],
                    "variant": variant,
                    "question": case["question"],
                    "response": result,
                    "grade": grade(case, manifest["gold"].get(case["id"]), result, documents.ids),
                }
                write_json(target, row)
                logger.info(
                    "phase7 case={} variant={} rule_passed={}",
                    case["id"],
                    variant,
                    row["grade"]["passed"],
                )
                summarize(out)
                if chat_transport.blocked or embedding_transport.blocked:
                    raise RuntimeError("Evaluation guard blocked further calls")
                if not response.success and any(
                    record.get("http_status", 0) >= 400 or record["status"] == "reserved"
                    for record in ledger.data["requests"][start_request:]
                ):
                    raise RuntimeError("Provider request failed; stop before further calls")
                if any(record.get("reservation_exceeded") for record in ledger.data["requests"]):
                    raise RuntimeError("Usage reservation exceeded; stop for review")
            finally:
                await agent.aclose()
    finally:
        try:
            await documents.close()
        finally:
            await embedder.aclose()
            await router.aclose()
            write_json(
                out / "run-metadata.json",
                {
                    "started_at": started,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "served_models": sorted(llm.served_models),
                    "scope": manifest["scope"],
                },
            )
            summarize(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/phase7"))
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send synthetic requests; requires separate human consent",
    )
    parser.add_argument(
        "--approved-synthetic-api-budget",
        action="store_true",
        help="Assert explicit consent for the documented API scope and budget",
    )
    parser.add_argument(
        "--regrade",
        action="store_true",
        help="按当前判分规则重新评分已保存的回答，不调用 API",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="本次最多新跑几道题（按固定顺序，已完成的跳过）；用于先试跑核对用量",
    )
    args = parser.parse_args()
    if args.live and not args.approved_synthetic_api_budget:
        parser.error("Live API evaluation requires explicit authorization")
    with exclusive_run(args.out):
        manifest, database = prepare(args.out)
        if args.regrade:
            print(json.dumps(regrade(args.out, manifest), ensure_ascii=False))
        elif args.live:
            asyncio.run(live(args.out, manifest, database, args.limit))
        else:
            print(
                json.dumps(
                    {
                        "mode": "offline_prepare",
                        "cases": len(CASES),
                        "gold_sql_queries": len(manifest["gold"]),
                        "fingerprint": manifest["fingerprint"],
                        "summary": summarize(args.out),
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
