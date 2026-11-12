"""Conservative deterministic checks: cell provenance, declared units and explicit periods.

This checks observable contradictions, not whether arbitrary SQL expresses the business question.
Missing metadata remains unverified rather than becoming a spurious pass or a guessed conversion.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

_CLAIM = re.compile(
    r"(?<![A-Za-z0-9_.+-])(?P<number>[+-]?\d[\d,，]*(?:\.\d+)?)\s*"
    r"(?P<unit>亿元|万元|元|分(?!钟)|个百分点|百分点|%|％|个|单|笔|人|件|条)(?![A-Za-z])"
)
_PERIOD = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|～|—)\s*(\d{4}-\d{2}-\d{2})")
_UNITS = {
    "元": ("money", Decimal(1)),
    "分": ("money", Decimal("0.01")),
    "万元": ("money", Decimal(10000)),
    "亿元": ("money", Decimal(100000000)),
    "%": ("percent", Decimal(1)),
    "％": ("percent", Decimal(1)),
    "百分比": ("percent", Decimal(1)),
    "比例": ("percent", Decimal(100)),
    "百分点": ("percentage_points", Decimal(1)),
    "个百分点": ("percentage_points", Decimal(1)),
    **{unit: (unit, Decimal(1)) for unit in ("个", "单", "笔", "人", "件", "条")},
}


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").replace("，", ""))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


@dataclass
class Fact:
    evidence_id: str
    path: str
    label: str
    value: str | None
    unit: str = ""
    unit_trusted: bool = True


@dataclass
class ValidationReport:
    status: str = "not_applicable"
    checks: list[dict[str, Any]] = field(default_factory=list)
    checked_claims: int = 0
    claim_count: int = 0
    limitations: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[str]:
        return [check["message"] for check in self.checks if check["status"] == "failed"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _column_unit(column: str) -> str:
    match = re.search(r"[（(](亿元|万元|元|分|%|百分点|单|笔|人|件|条)[）)]", column)
    if match:
        return match[1]
    suffixes = {"_yuan": "元", "_cents": "分", "_pct": "%", "_pp": "百分点"}
    return next((unit for suffix, unit in suffixes.items() if column.endswith(suffix)), "")


def _comparison_facts(value: Any, ref: str, label: str, unit: str, path: str) -> list[Fact]:
    facts = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in {
                "base_value",
                "current_value",
                "delta",
                "base_rate",
                "current_rate",
                "contribution_pp",
                "relative_change_pct",
                "relative_change_percent",
                "delta_pp",
            }:
                declared = (
                    "百分点"
                    if key.endswith("_pp")
                    else "%"
                    if "rate" in key or "pct" in key or "percent" in key
                    else unit
                )
                if item is None or _number(item) is not None:
                    facts.append(
                        Fact(
                            ref, child, label, None if item is None else str(item), declared, False
                        )
                    )
            elif key not in {"scope", "reconciliation", "warnings"}:
                facts.extend(_comparison_facts(item, ref, label, unit, child))
    elif isinstance(value, list):
        for index, item in enumerate(value[:200]):
            facts.extend(_comparison_facts(item, ref, label, unit, f"{path}[{index}]"))
    return facts


def collect_facts(steps) -> tuple[list[Fact], set[tuple[str, str]], list[str]]:
    facts: list[Fact] = []
    periods: set[tuple[str, str]] = set()
    limitations = []
    for ordinal, step in enumerate(steps, 1):
        if not step.ok or step.tool not in {
            "run_sql",
            "query_metric",
            "compare_periods",
            "query_business_metric",
        }:
            continue
        try:
            payload = json.loads(step.output)
        except (ValueError, TypeError):
            limitations.append(f"E{ordinal} 输出不完整，未作为程序校验证据。")
            continue
        if not isinstance(payload, dict) or payload.get("status") == "needs_clarification":
            continue
        ref = f"E{ordinal}"
        period = payload.get("period", {})
        if period.get("start") and period.get("end"):
            periods.add((period["start"], period["end"]))
        if step.tool == "run_sql":
            columns, rows = payload.get("columns", []), payload.get("rows", [])
            if payload.get("truncated") or payload.get("note", "").find("截断") >= 0:
                limitations.append(f"{ref} 查询结果被截断，不能验证完整统计范围。")
                continue
            for row_index, row in enumerate(rows[:200]):
                if not isinstance(row, list) or len(row) != len(columns):
                    continue
                for col_index, column in enumerate(columns):
                    if not isinstance(column, str) or re.search(
                        r"(^|_)(id|year|month|day|date)$", column, re.I
                    ):
                        continue
                    raw = row[col_index]
                    if raw is None or _number(raw) is not None:
                        label = re.sub(r"[（(][^）)]*[）)]|_(?:yuan|cents|pct|pp)$", "", column)
                        facts.append(
                            Fact(
                                ref,
                                f"rows[{row_index}][{col_index}]",
                                label,
                                None if raw is None else str(raw),
                                _column_unit(column),
                                False,
                            )
                        )
            continue
        definition = payload.get("metric", {})
        label, unit = definition.get("name", ""), definition.get("unit", "")
        if step.tool == "query_business_metric":
            for index, fact in enumerate(payload.get("facts", [])[:200]):
                if isinstance(fact, dict) and (
                    fact.get("value") is None or _number(fact["value"]) is not None
                ):
                    facts.append(
                        Fact(
                            ref,
                            f"facts[{index}].value",
                            fact.get("label", label),
                            fact.get("value"),
                            fact.get("unit", unit),
                        )
                    )
        elif step.tool == "query_metric" and "summary" in payload:
            summary = payload["summary"]
            facts.append(Fact(ref, "summary.value", label, summary.get("value"), unit))
            for index, group in enumerate(payload.get("groups", [])):
                value = group.get("value")
                if definition.get("kind") == "rate":
                    denominator, numerator = (
                        _number(group.get("denominator")),
                        _number(group.get("numerator")),
                    )
                    value = (
                        str(numerator / denominator * 100)
                        if denominator and numerator is not None
                        else None
                    )
                facts.append(Fact(ref, f"groups[{index}].value", label, value, unit))
        comparison = payload if step.tool == "compare_periods" else payload.get("comparison")
        if isinstance(comparison, dict):
            scope = comparison.get("scope", {})
            facts.extend(
                _comparison_facts(
                    comparison,
                    ref,
                    scope.get("metric", label),
                    scope.get("unit", unit),
                    "comparison",
                )
            )
            for prefix in ("base", "current"):
                if scope.get(prefix + "_start") and scope.get(prefix + "_end"):
                    periods.add((scope[prefix + "_start"], scope[prefix + "_end"]))
    return facts[:2000], periods, limitations


def _compatible(
    claim: Decimal, unit: str, fact: Fact, tolerance: Decimal, decreasing: bool
) -> bool:
    value = _number(fact.value)
    if value is None:
        return False
    if fact.unit:
        if fact.unit not in _UNITS or _UNITS[unit][0] != _UNITS[fact.unit][0]:
            return False
        value *= _UNITS[fact.unit][1]
    else:
        # Unknown units never authorize a guessed 100x conversion.
        value *= _UNITS[unit][1]
    expected = claim * _UNITS[unit][1]
    if decreasing and value < 0 and expected >= 0:
        value = abs(value)
    return abs(value - expected) <= tolerance * _UNITS[unit][1]


def validate_answer(answer: str, steps, *, needs_clarification: bool = False) -> ValidationReport:
    report = ValidationReport()
    report.limitations.append("仅自动识别带单位的数值断言；编号和无单位数字不作认证。")
    if needs_clarification:
        report.limitations.append("本轮待澄清，未对澄清文字作数值结论认证。")
        return report
    facts, periods, limitations = collect_facts(steps)
    report.limitations.extend(limitations)
    plain = re.sub(r"\*\*|__|`", "", answer)
    all_matches = list(_CLAIM.finditer(plain))
    matches = all_matches[:100]
    report.claim_count = len(all_matches)
    if len(all_matches) > 100:
        limitations.append("仅核对前 100 个数值断言，其余断言未验证。")
        report.limitations.append(limitations[-1])
    previous_end = 0
    for match in matches:
        claim = _number(match["number"])
        if claim is None:
            continue
        unit = match["unit"]
        # Bind to the nearest preceding label, including when multiple metrics share a line.
        prefix = plain[
            max(
                previous_end, plain.rfind("\n", 0, match.start()) + 1, match.start() - 100
            ) : match.start()
        ]
        previous_end = match.end()
        labels = {fact.label for fact in facts if fact.label and fact.label in prefix}
        closest = (
            max(labels, key=lambda label: (prefix.rfind(label) + len(label), len(label)))
            if labels
            else None
        )
        labelled = [fact for fact in facts if fact.label == closest] if closest else []
        candidates = labelled or facts
        number_text = match["number"].replace(",", "").replace("，", "")
        decimals = len(number_text.partition(".")[2])
        tolerance = Decimal(5).scaleb(-decimals - 1)
        decreasing = bool(
            re.search(r"下降|减少|降低|下跌", plain[max(0, match.start() - 12) : match.start()])
        )
        supported = [
            fact for fact in candidates if _compatible(claim, unit, fact, tolerance, decreasing)
        ]
        known = [fact for fact in candidates if fact.unit in _UNITS]
        if supported:
            checked = bool(labelled) and any(fact.unit and fact.unit_trusted for fact in supported)
            check_status = "supported" if checked else "unverified"
            message = (
                "数值与已确认单位可对应到工具结果单元格。"
                if checked
                else "数值可对应，SQL 别名或自由分析声明的单位仍需业务口径验证。"
            )
            if checked:
                report.checked_claims += 1
        elif labelled and known:
            check_status, message = (
                "failed",
                f"“{match[0]}”与同名指标的工具结果不一致，请核对数值、单位和分母。",
            )
        else:
            check_status, message = (
                "unverified",
                f"“{match[0]}”缺少可唯一核对的数值或单位，不能程序认证。",
            )
        report.checks.append(
            {
                "code": "numeric_provenance",
                "status": check_status,
                "claim": match[0],
                "message": message,
                "evidence_ids": list(dict.fromkeys(f.evidence_id for f in supported or known))[:12],
                "source_paths": [f"{f.evidence_id}.{f.path}" for f in supported or known][:12],
            }
        )
    for match in _PERIOD.finditer(plain):
        if not periods:
            break
        if not re.search(
            r"统计区间|统计范围|统计周期|口径|本期|当期|基期|数据范围|分析周期",
            plain[max(0, match.start() - 40) : match.start()],
        ):
            continue
        observed = (match[1], match[2])
        allowed = set(periods)
        for start, end in periods:
            try:
                allowed.add((start, (date.fromisoformat(end) - timedelta(days=1)).isoformat()))
            except ValueError:
                pass
        report.checks.append(
            {
                "code": "period_scope",
                "status": "supported" if observed in allowed else "failed",
                "claim": match[0],
                "message": "统计区间与工具口径一致。"
                if observed in allowed
                else "回答的明确日期区间未出现在本轮指标口径中。",
                "evidence_ids": [],
                "source_paths": [],
            }
        )
    if report.checks:
        report.status = (
            "failed"
            if report.issues
            else "partial"
            if any(check["status"] == "unverified" for check in report.checks) or limitations
            else "passed"
        )
        report.limitations.append(
            "程序仅核对可识别的数值、已声明单位和明确日期区间，不证明任意 SQL 的业务口径或因果解释正确。"
        )
    return report
