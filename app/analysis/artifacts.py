"""只从真实工具结果构建表格/图表；显示几何与精确数值分开。"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def numeric(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return bool(NUMBER.fullmatch(str(value))) and Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _table(identifier, title, labels, rows, evidence, scope=None, truncated=False):
    rows = [
        [
            str(value)
            if isinstance(value, Decimal) or isinstance(value, float) and not math.isfinite(value)
            else value
            for value in row
        ]
        for row in rows
    ]
    return {
        "id": identifier,
        "title": title,
        "columns": labels,
        "rows": rows,
        "evidence_ids": [evidence],
        "scope": scope or {},
        "truncated": truncated,
    }


def _chart(table, kind, unit, indexes, labels):
    if len(table["rows"]) < 2 or table["truncated"]:
        return None
    for row in table["rows"]:
        for index in indexes:
            if not numeric(row[index]) or abs(Decimal(str(row[index]))) > Decimal(
                "9007199254740991"
            ):
                return None
    return {
        "id": table["id"] + "-" + kind,
        "table_id": table["id"],
        "kind": kind,
        "title": table["title"],
        "unit": unit,
        "labels": [str(row[0]) if row[0] is not None else "未分组" for row in table["rows"]],
        "series": [
            {"name": name, "values": [str(row[index]) for row in table["rows"]]}
            for name, index in zip(labels, indexes)
        ],
        "evidence_ids": table["evidence_ids"],
        "scope": table["scope"],
    }


def build_artifacts(steps):
    tables, charts = [], []
    for ordinal, step in enumerate(steps, 1):
        if not step.ok or step.tool not in (
            "compare_periods",
            "query_metric",
            "run_sql",
            "query_business_metric",
        ):
            continue
        try:
            payload = json.loads(step.output)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        evidence, identifier = f"E{ordinal}", f"result-{ordinal}"
        comparison = payload if step.tool == "compare_periods" else payload.get("comparison")
        if comparison:
            scope, groups = comparison["scope"], comparison["groups"]
            rate, unit = scope["metric_kind"] == "rate", scope["unit"]
            columns = [
                scope["dimension"],
                f"基期（{unit}）",
                f"当期（{unit}）",
                "变化贡献（百分点）" if rate else f"变化（{unit}）",
            ]
            rows = [
                [
                    row["segment"],
                    row.get("base_rate" if rate else "base_value"),
                    row.get("current_rate" if rate else "current_value"),
                    row.get("contribution_pp" if rate else "delta"),
                ]
                for row in groups
            ]
            table = _table(
                identifier,
                scope["metric"] + " · " + scope["dimension"],
                columns,
                rows,
                evidence,
                scope,
            )
            tables.append(table)
            chart = _chart(table, "bar", unit, [1, 2], ["基期", "当期"])
            if chart:
                charts.append(chart)
            if comparison.get("reconciliation", {}).get("matched"):
                contribution = _chart(
                    table, "contribution", "百分点" if rate else unit, [3], ["变化贡献"]
                )
                if contribution:
                    charts.append(contribution)
        elif (
            step.tool in {"query_metric", "query_business_metric"}
            and payload.get("status") == "ready"
        ):
            definition = payload["metric"]
            rate = definition["kind"] == "rate"
            rows = []
            for row in payload.get("groups", []):
                value = row.get("value")
                if rate and step.tool == "query_metric":
                    denominator = row.get("denominator", 0)
                    value = (
                        str(Decimal(str(row["numerator"])) / Decimal(str(denominator)) * 100)
                        if denominator
                        else None
                    )
                rows.append([row["segment"], value])
            scope = {
                "metric": definition["name"],
                "unit": definition["unit"],
                "source": definition["source"],
                **payload.get("period", {}),
            }
            table = _table(
                identifier,
                definition["name"],
                ["分组", definition["name"] + "（" + definition["unit"] + "）"],
                rows,
                evidence,
                scope,
            )
            tables.append(table)
            chart = _chart(table, "bar", definition["unit"], [1], [definition["name"]])
            if chart:
                charts.append(chart)
        elif (
            step.tool == "run_sql"
            and isinstance(payload.get("columns"), list)
            and isinstance(payload.get("rows"), list)
        ):
            columns, rows = payload["columns"], payload["rows"]
            if not columns or any(
                not isinstance(row, list) or len(row) != len(columns) for row in rows
            ):
                continue
            table = _table(
                identifier,
                "查询结果 · " + evidence,
                columns,
                rows,
                evidence,
                truncated=bool(payload.get("truncated")),
            )
            tables.append(table)
            if len(columns) >= 2 and len(rows) >= 2 and _date_labels([row[0] for row in rows]):
                # 图表单独按日期排序，原始表格/导出保持查询返回顺序。
                ordered = {**table, "rows": sorted(rows, key=lambda row: str(row[0]))}
                selected = [
                    index
                    for index in range(1, min(4, len(columns)))
                    if all(numeric(row[index]) for row in rows)
                ]
                chart = (
                    _chart(
                        ordered,
                        "line",
                        "原始数值（单位未声明）",
                        selected,
                        [str(columns[index]) for index in selected],
                    )
                    if selected
                    else None
                )
                if chart:
                    charts.append(chart)
    return tables[-12:], charts[-12:]


def _date_labels(values):
    try:
        for value in values:
            text = str(value)
            candidate = text + "-01" if re.fullmatch(r"\d{4}-\d{2}", text) else text
            if date.fromisoformat(candidate).isoformat() != candidate:
                return False
        return len(set(map(str, values))) == len(values)
    except ValueError:
        return False


def confirmed_state(steps, previous):
    """条件来自工具，数值不作为下轮证据；不从模型自由文字提取状态。"""
    state = deepcopy(previous or {})
    for ordinal, step in enumerate(steps, 1):
        if not step.ok:
            continue
        try:
            payload = json.loads(step.output)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        scope = None
        if (
            step.tool in ("prepare_analysis", "compare_periods")
            and payload.get("status") == "ready"
        ):
            scope = payload.get("scope")
        elif (
            step.tool in {"query_metric", "query_business_metric"}
            and payload.get("status") == "ready"
        ):
            state.pop("dataset_table", None)
            scope = {
                **step.arguments,
                "metric": payload["metric"]["name"],
                "unit": payload["metric"]["unit"],
                "source": payload["metric"]["source"],
                "metric_version": payload["metric"]["version"],
                "dimension": step.arguments.get("dimension", "overall"),
            }
        elif step.tool == "profile_dataset" and payload.get("status") in ("ready", "warning"):
            if state.get("dataset_table") != payload["table"]:
                state.pop("scope", None)
            state["dataset_table"] = payload["table"]
        if scope:
            state["scope"] = scope
            state["evidence_id"] = f"E{ordinal}"
    return state
