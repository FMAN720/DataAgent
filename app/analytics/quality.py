"""只读数据体检：标识符来自实际表结构，统计全量聚合，输出有界。"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.engine import AnalyticsDatabase, QueryResult, TableInfo
from app.core.tools.base import BaseTool, ToolError


def iso_day(value: str) -> date:
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("日期须为 YYYY-MM-DD")
    return result


def coverage_warnings(first: Any, last: Any, start: str, end: str) -> list[str]:
    if first is None or last is None:
        return ["日期字段没有可用范围，无法确认分析周期。"]
    try:
        low, high = iso_day(str(first)[:10]), iso_day(str(last)[:10])
    except ValueError:
        return ["日期范围不是有效 ISO 日期，需核对源字段。"]
    messages = []
    if start and iso_day(start) < low:
        messages.append(f"请求开始日 {start} 早于已观察到的数据开始日 {low}。")
    if end and (iso_day(end) - high).days > 1:
        messages.append(f"请求结束日 {end} 超出已观察到的数据末日 {high}；需确认是否尚未导入完整。")
    return messages


def scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")[:160]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value[:160] if isinstance(value, str) else value


def row_dict(result: QueryResult) -> dict[str, Any]:
    if result.truncated or len(result.rows) != 1:
        raise ToolError("质量统计必须完整返回一行，不能使用截断结果")
    return dict(zip(result.columns, result.rows[0]))


def quoted_table(db: AnalyticsDatabase, info: TableInfo) -> str:
    quote = db._engine.dialect.identifier_preparer.quote_identifier
    return ".".join(quote(value) for value in (info.schema, info.name) if value is not None)


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    table: str = Field(min_length=1, max_length=180)
    columns: list[str] = Field(default_factory=list, max_length=8)
    key_columns: list[str] = Field(default_factory=list, max_length=3)
    date_column: str = Field(default="", max_length=128)
    expected_start: str = ""
    expected_end: str = ""

    @model_validator(mode="after")
    def validate_period(self):
        for value in (self.expected_start, self.expected_end):
            if value:
                iso_day(value)
        if bool(self.expected_start) != bool(self.expected_end):
            raise ValueError("检查周期需同时提供开始与结束日")
        if self.expected_start and (
            not self.date_column or self.expected_start >= self.expected_end
        ):
            raise ValueError("周期检查需要日期字段和有效的左闭右开区间")
        if len(set(self.columns)) != len(self.columns) or len(set(self.key_columns)) != len(
            self.key_columns
        ):
            raise ValueError("字段不能重复")
        return self


class ProfileDatasetTool(BaseTool):
    name = "profile_dataset"
    description = (
        "对业务或已导入表执行只读数据体检：行数、缺失/空白、唯一值、数值范围及负值、"
        "期望唯一键的重复数、日期边界。key_columns 只填写应当唯一的键；普通分组列不应当唯一。"
        "日期边界覆盖不证明数据完整；不自动删除、填补或转换数据。最多检查8个字段。"
    )

    def __init__(self, database: AnalyticsDatabase):
        super().__init__()
        self.database = database

    def schema_parameters(self):
        return ProfileRequest.model_json_schema()

    async def execute(self, **kwargs):
        request = ProfileRequest.model_validate(kwargs)
        found, missing = await self.database.describe([request.table])
        if missing:
            raise ToolError("体检表不存在或不在可见 schema 中")
        info = found[0]
        available = {column.name: column for column in info.columns}
        columns = request.columns or list(available)[:8]
        selected = [
            *columns,
            *request.key_columns,
            *([request.date_column] if request.date_column else []),
        ]
        if any(name not in available for name in selected):
            raise ToolError("体检字段不存在；请先查看表结构，不能提交 SQL 表达式")
        quote = self.database._engine.dialect.identifier_preparer.quote_identifier
        table = quoted_table(self.database, info)
        expressions = ['COUNT(*) AS "rows"']
        if request.key_columns:
            invalid_keys = " OR ".join(
                f"{quote(name)} IS NULL OR TRIM(CAST({quote(name)} AS TEXT)) = ''"
                for name in request.key_columns
            )
            expressions.append(f"SUM(CASE WHEN {invalid_keys} THEN 1 ELSE 0 END) AS missing_keys")
        numeric = {}
        for index, name in enumerate(columns):
            column = quote(name)
            expressions.extend(
                [
                    f"SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS n{index}",
                    f"SUM(CASE WHEN {column} IS NOT NULL AND TRIM(CAST({column} AS TEXT)) = '' THEN 1 ELSE 0 END) AS b{index}",
                    f"COUNT(DISTINCT {column}) AS d{index}",
                ]
            )
            kind = available[name].type.upper()
            numeric[name] = any(
                token in kind for token in ("INT", "REAL", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE")
            )
            if numeric[name]:
                invalid = (
                    f"typeof({column}) NOT IN ('integer','real')"
                    if self.database.dialect == "sqlite"
                    else f"CAST({column} AS TEXT) IN ('NaN','Infinity','-Infinity')"
                )
                valid = f"{column} IS NOT NULL AND NOT ({invalid})"
                expressions.extend(
                    [
                        f"SUM(CASE WHEN {column} IS NOT NULL AND ({invalid}) THEN 1 ELSE 0 END) AS x{index}",
                        f"MIN(CASE WHEN {valid} THEN {column} END) AS lo{index}",
                        f"MAX(CASE WHEN {valid} THEN {column} END) AS hi{index}",
                        f"SUM(CASE WHEN {valid} AND {column} < 0 THEN 1 ELSE 0 END) AS neg{index}",
                    ]
                )
        if request.date_column:
            column = quote(request.date_column)
            expressions += [
                f"MIN(CAST({column} AS TEXT)) AS first_date",
                f"MAX(CAST({column} AS TEXT)) AS last_date",
            ]
        queries = ["SELECT " + ", ".join(expressions) + " FROM " + table]
        if request.key_columns:
            keys = ", ".join(quote(name) for name in request.key_columns)
            queries.append(
                f"SELECT COALESCE(SUM(n - 1), 0) AS duplicates FROM (SELECT COUNT(*) AS n FROM {table} GROUP BY {keys} HAVING COUNT(*) > 1) AS duplicate_keys"
            )
        results = await self.database.execute_many(queries)
        values = row_dict(results[0])
        count = int(values["rows"])
        warnings, questions, summaries = [], [], []
        if not count:
            questions.append("数据表为空，请确认导入是否完成或选择其他来源。")
        for index, name in enumerate(columns):
            nulls, blanks = int(values[f"n{index}"] or 0), int(values[f"b{index}"] or 0)
            summary = {
                "column": name,
                "type": available[name].type,
                "null_count": nulls,
                "blank_count": blanks,
                "missing_pct": round((nulls + blanks) * 100 / count, 4) if count else None,
                "distinct_count": int(values[f"d{index}"]),
            }
            if nulls or blanks:
                warnings.append(f"字段 {name} 存在 {nulls + blanks} 个缺失或空白值。")
            if numeric[name]:
                summary.update(
                    min=scalar(values[f"lo{index}"]),
                    max=scalar(values[f"hi{index}"]),
                    negative_count=int(values[f"neg{index}"] or 0),
                    invalid_numeric_count=int(values[f"x{index}"] or 0),
                )
                if summary["negative_count"]:
                    warnings.append(
                        f"字段 {name} 含负数；需结合退款、冲销等业务语义判断，未自动判错。"
                    )
                if summary["invalid_numeric_count"]:
                    warnings.append(f"字段 {name} 含非有效数值，不能直接用于统计。")
            summaries.append(summary)
        duplicates = int(row_dict(results[1])["duplicates"]) if request.key_columns else None
        missing_keys = int(values.get("missing_keys") or 0) if request.key_columns else None
        if duplicates:
            questions.append(f"期望唯一键存在 {duplicates} 条多余重复记录，请核对键定义或源数据。")
        if missing_keys:
            questions.append(f"期望唯一键有 {missing_keys} 行缺失或空白，无法可靠标识记录。")
        dates = None
        if request.date_column:
            dates = {
                "column": request.date_column,
                "min": scalar(values["first_date"]),
                "max": scalar(values["last_date"]),
                "completeness": "unverified",
            }
            if request.expected_start:
                questions.extend(
                    coverage_warnings(
                        values["first_date"],
                        values["last_date"],
                        request.expected_start,
                        request.expected_end,
                    )
                )
            warnings.append("日期边界仅表示观察范围，不能证明中间没有漏日、漏单或延迟导入。")
        return {
            "status": "needs_clarification" if questions else "warning" if warnings else "ready",
            "table": info.qualified_name,
            "row_count": count,
            "columns": summaries,
            "columns_truncated": len(columns) < len(info.columns),
            "duplicate_excess_rows": duplicates,
            "key_columns": request.key_columns,
            "key_missing_rows": missing_keys,
            "date_range": dates,
            "warnings": warnings,
            "questions": questions,
            "method": "full_table_aggregates_readonly_snapshot",
        }
