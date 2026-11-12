"""Bounded evidence packets and UI summaries derived exclusively from actual tool steps."""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceBundle:
    records: list[str] = field(default_factory=list)
    valid_ids: set[str] = field(default_factory=set)
    complete: bool = True
    omitted: list[str] = field(default_factory=list)


def collect_evidence(steps, budget: int = 12000) -> EvidenceBundle:
    bundle = EvidenceBundle()
    used = 0
    for index, step in enumerate(steps, 1):
        ref = f"E{index}"
        structured = step.tool in (
            "run_sql",
            "compare_periods",
            "prepare_analysis",
            "search_documents",
            "profile_dataset",
            "query_metric",
            "get_metric_definition",
            "query_business_metric",
            "get_business_definition",
        )
        truncated = "...[输出过长，已截断" in step.output
        try:
            output = json.loads(step.output) if structured and not truncated else step.output
        except (ValueError, TypeError):
            output, truncated = "工具输出不是完整 JSON，不能作为完整证据", True
        record = json.dumps(
            {
                "evidence_id": ref,
                "tool": step.tool,
                "ok": step.ok,
                "arguments": step.arguments,
                "output": output,
            },
            ensure_ascii=False,
        )
        if truncated or used + len(record) + 40 > budget:
            bundle.complete = False
            bundle.omitted.append(ref)
            continue
        bundle.records.append(record)
        if step.ok:
            bundle.valid_ids.add(ref)
        used += len(record) + 40
    if bundle.omitted:
        bundle.records.append("证据不完整，以下记录未能完整纳入审查：" + ", ".join(bundle.omitted))
    return bundle


def analysis_summary(steps, *, needs_clarification=False, reflection=None) -> dict[str, Any]:
    scope, questions, comparisons, warnings = None, [], [], []
    quality_checks, metrics = [], []
    for step in steps:
        if not step.ok or step.tool not in (
            "prepare_analysis",
            "compare_periods",
            "profile_dataset",
            "query_metric",
            "query_business_metric",
        ):
            continue
        try:
            payload = json.loads(step.output)
        except (ValueError, TypeError):
            warnings.append("分析工具输出不完整，无法展示可核对的拆解。")
            continue
        if step.tool == "profile_dataset":
            quality_checks.append(payload)
            warnings.extend(payload.get("warnings", []))
            warnings.extend(payload.get("questions", []))
            questions.extend(payload.get("questions", []))
            continue
        if step.tool in {"query_metric", "query_business_metric"}:
            metrics.append(payload)
            warnings.extend(payload.get("warnings", []))
            questions.extend(payload.get("questions", []))
            if payload.get("comparison"):
                comparisons.append(payload["comparison"])
                scope = payload["comparison"]["scope"]
                warnings.extend(payload["comparison"].get("warnings", []))
            continue
        scope = payload.get("scope", scope)
        if step.tool == "prepare_analysis":
            questions = payload.get("questions", [])
        else:
            comparisons.append(payload)
            warnings.extend(payload.get("warnings", []))
    status = "needs_clarification" if needs_clarification else "unverified"
    if needs_clarification:
        # Earlier attempts remain in the trace, not as a confirmed comparison card.
        comparisons = []
    if reflection and not needs_clarification:
        status = (
            "reviewed"
            if reflection.status.value == "passed"
            else "review_" + reflection.status.value
        )
        if reflection.status.value == "failed":
            warnings.append("最终答案仍未通过证据审查，请核对审查问题后再使用。")
        if reflection.status.value == "unavailable":
            warnings.append("证据审查未完成，当前答案尚未核验。")
    return {
        "status": status,
        "scope": scope,
        "clarification_questions": questions,
        "comparisons": comparisons[-4:],
        "warnings": list(dict.fromkeys(warnings)),
        "comparison_count": len(comparisons),
        "quality_checks": quality_checks[-3:],
        "metrics": metrics[-3:],
    }
