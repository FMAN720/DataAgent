"""Execute registered single-fact-table metrics using bound values and one read-only snapshot."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field, model_validator

from app.analytics.comparison import serialise
from app.analytics.engine import AnalyticsDatabase, SQLValidationError
from app.analytics.metrics import QualityGate
from app.analytics.quality import iso_day, quoted_table, row_dict
from app.analytics.semantics import ACTIVE_PROFILE, BusinessMetric, ContractModel, MetricFilter
from app.core.tools.base import BaseTool, ToolError


class DefinitionQuery(ContractModel):
    metric_id: str | None = Field(default=None, max_length=64)


class BusinessMetricQuery(ContractModel):
    metric_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    start: str
    end: str
    dimension: str | None = Field(default=None, max_length=80)
    filters: list[MetricFilter] = Field(default_factory=list, max_length=4)
    order: Literal["value_desc", "value_asc", "segment"] = "value_desc"

    @model_validator(mode="after")
    def period(self):
        iso_day(self.start)
        iso_day(self.end)
        if self.start >= self.end:
            raise ValueError("周期为左闭右开，开始日须早于结束日")
        return self


def selected_metric(identifier: str) -> tuple[BusinessMetric, dict]:
    snapshot = ACTIVE_PROFILE.get()
    if snapshot is None or snapshot.profile is None:
        raise ToolError("请先在会话中选择一份自定义业务口径")
    for metric in snapshot.profile.metrics:
        if metric.id == identifier:
            return metric, snapshot.reference
    raise ToolError("该指标不属于本次选定的业务口径，请先查看已注册指标")


class GetBusinessDefinitionTool(BaseTool):
    name = "get_business_definition"
    description = "查看当前会话选定的自定义业务口径及已注册指标。省略 metric_id 只列名称；指定后返回字段、单位、唯一键、维度和固定筛选。"

    def schema_parameters(self):
        return DefinitionQuery.model_json_schema()

    async def execute(self, **kwargs):
        request = DefinitionQuery.model_validate(kwargs)
        snapshot = ACTIVE_PROFILE.get()
        if snapshot is None or snapshot.profile is None:
            raise ToolError("当前会话未选择自定义业务口径")
        if request.metric_id:
            metric, reference = selected_metric(request.metric_id)
            value = {**metric.model_dump(), "unit": metric.unit, "profile_ref": reference}
            if len(json.dumps(value, ensure_ascii=False)) > 7000:
                raise ToolError("此指标定义超过工具输出预算，请简化筛选或说明后再查询")
            return value
        return {
            "profile_ref": snapshot.reference,
            "metrics": [
                {"id": metric.id, "name": metric.name, "unit": metric.unit}
                for metric in snapshot.profile.metrics
            ],
        }


def filter_sql(filters: list[MetricFilter], quote) -> tuple[list[str], dict]:
    predicates, parameters = [], {}
    for ordinal, condition in enumerate(filters):
        column = quote(condition.column)
        if condition.operator in {"is_null", "not_null"}:
            predicates.append(
                column + (" IS NULL" if condition.operator == "is_null" else " IS NOT NULL")
            )
            continue
        names = []
        for index, value in enumerate(condition.values):
            key = f"filter_{ordinal}_{index}"
            parameters[key] = value
            names.append(":" + key)
        if condition.operator in {"in", "not_in"}:
            predicates.append(
                f"{column} {'NOT IN' if condition.operator == 'not_in' else 'IN'} ({','.join(names)})"
            )
        else:
            predicates.append(f"{column} {'<>' if condition.operator == 'ne' else '='} {names[0]}")
    return predicates, parameters


def result_value(row: dict, metric: BusinessMetric) -> Decimal | None:
    if not row["record_count"]:
        return None
    numerator = Decimal(str(row["numerator"]))
    denominator = Decimal(str(row["denominator"])) if row["denominator"] is not None else Decimal(0)
    if metric.aggregation in {"ratio", "average"}:
        value = numerator / denominator if denominator else None
    else:
        value = numerator
    if value is None:
        return None
    value *= {
        "分": Decimal("0.01"),
        "万元": Decimal(10000),
        "亿元": Decimal(100000000),
        "比例": Decimal(100),
    }.get(metric.storage_unit, Decimal(1))
    if not value.is_finite() or abs(value) > Decimal("1e24"):
        raise ToolError("指标结果非有限或超出支持范围，请核对数据和分母")
    return value


class QueryBusinessMetricTool(BaseTool):
    name = "query_business_metric"
    description = (
        "按当前已选业务口径查询已注册指标。先 get_business_definition；SQL 和单位转换由程序生成。"
        "start/end 为左闭右开周期；dimension 和 filters 仅使用注册的字段；支持求和、计数、均值、总分子/总分母比例。"
        "会检查唯一键、缺失、日期和数值。empty 表示无记录，不能写成0；比例分母为0时未定义。"
        "分组按 order 排序，最多展示前8组，总量始终独立计算。对比可分别查询两个周期。"
    )

    def __init__(self, database: AnalyticsDatabase):
        super().__init__()
        self.database = database

    def schema_parameters(self):
        return BusinessMetricQuery.model_json_schema()

    async def execute(self, **kwargs):
        request = BusinessMetricQuery.model_validate(kwargs)
        metric, reference = selected_metric(request.metric_id)
        if (
            metric.time_behavior == "snapshot"
            and (date.fromisoformat(request.end) - date.fromisoformat(request.start)).days != 1
        ):
            raise ToolError("快照型指标只允许单日查询，请明确快照日期；库存或余额不能跨日累加")
        if self.database.dialect not in {"sqlite", "postgresql"}:
            raise ToolError("业务口径查询目前支持 SQLite 和 PostgreSQL")
        if request.dimension and request.dimension not in metric.dimensions:
            raise ToolError("此维度未在当前业务口径中注册")
        if any(
            condition.column not in {*metric.dimensions, *metric.filter_columns}
            for condition in request.filters
        ):
            raise ToolError("运行时筛选只能使用口径中允许的维度和筛选字段")
        found, missing = await self.database.describe([metric.table])
        if missing:
            raise ToolError("业务口径绑定的表已不可用，请更新口径映射")
        table = found[0]
        columns = {column.name: column for column in table.columns}
        measures = [
            column
            for column in (metric.value_column, metric.numerator_column, metric.denominator_column)
            if column
        ]
        required = {*metric.primary_key, metric.date_column, *measures}
        configured = (
            required
            | set(metric.dimensions)
            | set(metric.filter_columns)
            | set(metric.non_negative_columns)
            | {condition.column for condition in metric.filters}
        )
        if not configured <= columns.keys():
            raise ToolError("业务字段与口径版本不匹配，请更新映射；不能自动改用同名字段")
        for name in {*measures, *metric.non_negative_columns}:
            if not any(
                token in columns[name].type.upper()
                for token in ("INT", "DECIMAL", "NUMERIC", "REAL", "FLOAT", "DOUBLE")
            ):
                raise ToolError("指标或非负检查字段不是已确认的数值类型，请先核对导入字段")
        quote = self.database._engine.dialect.identifier_preparer.quote_identifier
        source = quoted_table(self.database, table)
        conditions, parameters = filter_sql([*metric.filters, *request.filters], quote)
        all_rows = " AND ".join(conditions) or "1=1"
        date_column = quote(metric.date_column)
        numeric_checks = []
        for name in measures:
            column = quote(name)
            numeric_checks.append(f"ABS({column}) > 1e24")
            if self.database.dialect == "sqlite":
                numeric_checks.append(f"typeof({column}) NOT IN ('integer','real')")
            else:
                numeric_checks.append(f"CAST({column} AS TEXT) IN ('NaN','Infinity','-Infinity')")
        numeric_checks.extend(f"{quote(name)} < 0" for name in metric.non_negative_columns)
        invalid = [*(f"{quote(name)} IS NULL" for name in required), *numeric_checks]
        if self.database.dialect == "sqlite" or not any(
            token in columns[metric.date_column].type.upper() for token in ("DATE", "TIME")
        ):
            if self.database.dialect == "sqlite":
                invalid.append(
                    f"SUBSTR({date_column},1,4)='0000' OR CASE WHEN LENGTH({date_column})=10 THEN date({date_column}, '+0 days') IS NULL OR date({date_column}, '+0 days') != {date_column} WHEN LENGTH({date_column})=19 THEN strftime('%Y-%m-%d %H:%M:%S',{date_column},'+0 seconds') IS NULL OR strftime('%Y-%m-%d %H:%M:%S',{date_column},'+0 seconds') != {date_column} ELSE 1 END"
                )
            else:
                # Invalid calendar dates raise safely; no aggregate is then returned.
                invalid.append(
                    f"CASE WHEN {date_column} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' THEN CAST({date_column} AS DATE) IS NULL ELSE TRUE END"
                )
        keys = ",".join(quote(name) for name in metric.primary_key)
        duplicates = f"SELECT COUNT(*) FROM (SELECT {keys} FROM {source} WHERE {all_rows} GROUP BY {keys} HAVING COUNT(*)>1) duplicate_keys"
        quality_sql = f"SELECT COUNT(*) AS source_rows, SUM(CASE WHEN {' OR '.join(invalid)} THEN 1 ELSE 0 END) AS invalid_rows, ({duplicates}) AS duplicate_keys FROM {source} WHERE {all_rows}"
        parameters.update(period_start=request.start, period_end=request.end)
        scope = f"{all_rows} AND {date_column}>=:period_start AND {date_column}<:period_end"
        numerator = (
            "COUNT(*)"
            if metric.aggregation == "count"
            else f"SUM({quote(metric.numerator_column if metric.aggregation == 'ratio' else metric.value_column)})"
        )
        denominator = (
            f"SUM({quote(metric.denominator_column)})"
            if metric.aggregation == "ratio"
            else "COUNT(*)"
            if metric.aggregation == "average"
            else "1"
        )
        aggregate = (
            f"{numerator} AS numerator, {denominator} AS denominator, COUNT(*) AS record_count"
        )
        total_sql = f"SELECT {aggregate} FROM {source} WHERE {scope}"
        segment = quote(request.dimension) if request.dimension else "'整体'"
        order = (
            "segment"
            if request.order == "segment"
            else (
                f"{numerator} * 1.0 / NULLIF({denominator},0)"
                if metric.aggregation in {"ratio", "average"}
                else numerator
            )
            + (" ASC" if request.order == "value_asc" else " DESC")
        )
        detail_sql = f"SELECT {segment} AS segment, {aggregate} FROM {source} WHERE {scope} GROUP BY 1 ORDER BY {order}"
        quality = {}

        def check_quality(result):
            quality.update(row_dict(result))
            if quality.get("invalid_rows") or quality.get("duplicate_keys"):
                raise QualityGate(
                    {
                        "status": "needs_clarification",
                        "metric": {"name": metric.name, "unit": metric.unit},
                        "profile_ref": reference,
                        "quality": quality,
                        "questions": [
                            "源数据存在重复唯一键、缺失、非法日期、非法数值或违反已确认的非负约束，请先核对数据。"
                        ],
                        "warnings": [],
                        "result": None,
                    }
                )

        try:
            quality_result, total, detail = await self.database.execute_snapshot(
                [quality_sql, total_sql, detail_sql],
                validate_first=check_quality,
                parameters=parameters,
            )
        except QualityGate as exc:
            return exc.payload
        except SQLValidationError:
            raise ToolError(
                "业务口径查询未完成，请核对字段类型、日期格式与数据服务；未返回统计结论"
            ) from None
        if total.truncated or detail.truncated:
            raise ToolError("分组超过查询预算，请缩小范围或使用较粗维度，不能依据不完整结果下结论")
        summary = row_dict(total)
        groups = [dict(zip(detail.columns, row)) for row in detail.rows]
        with localcontext() as context:
            context.prec = 60
            value = result_value(summary, metric)
            displayed = [
                {"segment": row["segment"], "value": result_value(row, metric)}
                for row in groups[:8]
            ]
            payload = serialise(
                {
                    "status": "ready" if summary["record_count"] else "empty",
                    "metric": {
                        "id": metric.id,
                        "name": metric.name,
                        "unit": metric.unit,
                        "aggregation": metric.aggregation,
                        "kind": (
                            "rate"
                            if metric.aggregation == "ratio"
                            else "non_additive"
                            if metric.aggregation == "average"
                            else "additive"
                        ),
                        "version": reference["revision"],
                        "source": reference["id"],
                    },
                    "profile_ref": reference,
                    "table": table.qualified_name,
                    "period": {"start": request.start, "end": request.end},
                    "filters": [
                        condition.model_dump() for condition in [*metric.filters, *request.filters]
                    ],
                    "summary": {"value": value, "record_count": summary["record_count"]},
                    "groups": displayed,
                    "group_count": len(groups),
                    "quality": quality,
                    "warnings": ["日期和唯一键检查不证明业务数据已全部入库。"],
                    "query": detail_sql,
                }
            )
        if value is None:
            payload["warnings"].append("没有记录或分母为零，指标未定义，不能写成业务零值。")
        while True:
            payload["facts"] = [
                {
                    "label": metric.name,
                    "value": payload["summary"]["value"],
                    "unit": metric.unit,
                    "segment": "整体",
                },
                *[
                    {
                        "label": metric.name,
                        "value": row["value"],
                        "unit": metric.unit,
                        "segment": row["segment"],
                    }
                    for row in payload["groups"]
                ],
            ]
            if len(json.dumps(payload, ensure_ascii=False)) <= 7000:
                break
            if not payload["groups"]:
                raise ToolError("此口径超出工具证据预算，请简化筛选或缩短字段名称")
            payload["groups"].pop()
        if len(payload["groups"]) < len(groups):
            payload["warnings"].append(
                "仅展示排序后的部分分组；整体值独立计算，不能把展示组当作完整明细。"
            )
        return payload
