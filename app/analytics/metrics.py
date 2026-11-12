"""版本化电商指标契约；SQL 由程序生成，质量门禁与统计使用同一只读快照。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analytics.comparison import AnalysisScope, compare_results, serialise
from app.analytics.engine import AnalyticsDatabase
from app.analytics.quality import coverage_warnings, iso_day, quoted_table, row_dict
from app.core.tools.base import BaseTool, ToolError

SOURCE = "ecommerce_v1"
VERSION = "1"


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    name: str
    unit: str
    formula: str
    kind: str = "additive"

    def public(self):
        return {
            **self.__dict__,
            "version": VERSION,
            "source": SOURCE,
            "time_field": "orders.order_time",
            "exclude_test_users": True,
            "allowed_join": "orders.user_id = users.user_id（users.user_id 必须唯一）",
            "dimensions": ["overall", "region", "order_channel"],
            "scope": "项目电商模型；不自动套用于上传表或其他数据库业务口径",
        }


METRICS = {
    "net_sales": MetricDefinition(
        "net_sales", "净销售额", "元", "有效订单 pay_amount（分）求和 / 100；paid、completed"
    ),
    "order_count": MetricDefinition(
        "order_count", "有效订单数", "单", "paid、completed 的订单数；一行一单"
    ),
    "refund_rate": MetricDefinition(
        "refund_rate",
        "退款率",
        "%",
        "refunded 订单数 / paid、completed、refunded 订单数 × 100",
        "rate",
    ),
}


class DefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric_id: Literal["net_sales", "order_count", "refund_rate"] | None = None


class GetMetricDefinitionTool(BaseTool):
    name = "get_metric_definition"
    description = "查看固定的电商指标定义、版本、单位、时间字段和允许关联。支持净销售额、有效订单数、退款率；这些定义不适用于任意上传表。"

    def schema_parameters(self):
        return DefinitionRequest.model_json_schema()

    async def execute(self, **kwargs):
        request = DefinitionRequest.model_validate(kwargs)
        return {
            "definitions": [
                definition.public()
                for key, definition in METRICS.items()
                if request.metric_id is None or key == request.metric_id
            ]
        }


class MetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric_id: Literal["net_sales", "order_count", "refund_rate"]
    source: str = Field(default="", max_length=120)
    start: str = ""
    end: str = ""
    base_start: str = ""
    base_end: str = ""
    dimension: Literal["overall", "region", "order_channel"] = "overall"
    unit: str = Field(default="", max_length=16)
    region_code: str | None = Field(default=None, pattern=r"^[A-Z_]{1,32}$")
    order_channel: Literal["app", "web", "mini_program"] | None = None

    @model_validator(mode="after")
    def periods(self):
        for start, end in ((self.start, self.end), (self.base_start, self.base_end)):
            for value in (start, end):
                if value:
                    iso_day(value)
            if start and end and start >= end:
                raise ValueError("周期须为左闭右开区间，开始日早于结束日")
        if all((self.start, self.end, self.base_start, self.base_end)):
            if max(self.start, self.base_start) < min(self.end, self.base_end):
                raise ValueError("对比周期不能重叠")
        return self


class QualityGate(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__("指标前置检查未通过")


def _invalid_timestamp(dialect: str) -> str:
    column = 'o."order_time"'
    if dialect == "sqlite":
        return (
            f"{column} IS NULL OR LENGTH({column}) != 19 OR SUBSTR({column},1,4) = '0000' OR "
            f"strftime('%Y-%m-%d %H:%M:%S', {column}, '+0 seconds') IS NULL OR "
            f"strftime('%Y-%m-%d %H:%M:%S', {column}, '+0 seconds') != {column}"
        )
    pattern = r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) ([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$"
    year = f"CAST(SUBSTR({column},1,4) AS INTEGER)"
    leap = f"({year} % 400 = 0 OR ({year} % 4 = 0 AND {year} % 100 != 0))"
    days = (
        f"CASE WHEN SUBSTR({column},6,2) = '02' THEN CASE WHEN {leap} THEN 29 ELSE 28 END "
        f"WHEN SUBSTR({column},6,2) IN ('04','06','09','11') THEN 30 ELSE 31 END"
    )
    # CASE 防止在任意脏字符串上执行整数/日期转换。
    return (
        f"CASE WHEN {column} ~ '{pattern}' AND SUBSTR({column},1,4) != '0000' "
        f"THEN CASE WHEN CAST(SUBSTR({column},9,2) AS INTEGER) <= {days} THEN 0 ELSE 1 END ELSE 1 END = 1"
    )


def _gate_payload(definition, questions, quality=None):
    return {
        "status": "needs_clarification",
        "metric": definition.public(),
        "questions": questions,
        "quality": quality or {},
        "warnings": questions,
        "result": None,
    }


def _request_questions(request: MetricRequest, definition: MetricDefinition) -> list[str]:
    """请求缺少来源、周期或单位不符时，需要先向用户确认的问题。"""
    questions = []
    if request.source != SOURCE:
        questions.append(
            "请确认使用项目电商业务模型 ecommerce_v1；上传数据须另行确认字段、单位与口径。"
        )
    if not request.start or not request.end:
        questions.append("请补充当期开始日和结束日（结束日不包含）。")
    if bool(request.base_start) != bool(request.base_end):
        questions.append("对比时请同时提供基期开始日与结束日。")
    if request.unit and request.unit != definition.unit:
        questions.append(
            f"指标 {definition.name} 的固定输出单位为 {definition.unit}，请核对请求单位。"
        )
    return questions


def _quality_sql(dialect: str, order_table: str, user_table: str) -> str:
    """全表体检：重复键、缺失、未知状态、无效时间与金额、孤立订单、日期边界。"""
    invalid_money = (
        "typeof(o.pay_amount) != 'integer'"
        if dialect == "sqlite"
        else "CAST(o.pay_amount AS TEXT) IN ('NaN','Infinity','-Infinity')"
    )
    money_check = f"CASE WHEN o.pay_amount IS NULL OR ({invalid_money}) THEN 1 WHEN o.pay_amount < 0 OR o.pay_amount != ROUND(o.pay_amount) THEN 1 ELSE 0 END"
    return f"""SELECT a.*, b.*,
        (SELECT COUNT(*) FROM {order_table} x WHERE NOT EXISTS
            (SELECT 1 FROM {user_table} y WHERE y.user_id=x.user_id)) AS orphan_orders
        FROM (SELECT COUNT(*) AS order_rows, COUNT(order_id)-COUNT(DISTINCT order_id) AS duplicate_orders,
            SUM(CASE WHEN order_id IS NULL OR user_id IS NULL OR status IS NULL THEN 1 ELSE 0 END) AS missing_orders,
            SUM(CASE WHEN status NOT IN ('paid','completed','refunded','cancelled') THEN 1 ELSE 0 END) AS invalid_status,
            SUM(CASE WHEN {_invalid_timestamp(dialect)} THEN 1 ELSE 0 END) AS invalid_dates,
            SUM({money_check}) AS invalid_money,
            MIN(order_time) AS first_date, MAX(order_time) AS last_date FROM {order_table} o) a
        CROSS JOIN (SELECT COUNT(user_id)-COUNT(DISTINCT user_id) AS duplicate_users,
            SUM(CASE WHEN user_id IS NULL OR is_test IS NULL OR is_test NOT IN (0,1) THEN 1 ELSE 0 END) AS invalid_users
            FROM {user_table}) b"""


def _metric_sql(
    request: MetricRequest, definition: MetricDefinition, order_table: str, user_table: str
) -> tuple[str, str, bool]:
    """程序生成的分组统计 SQL 与独立总量 SQL；返回 (分组 SQL, 总量 SQL, 是否为双周期对比)。

    日期、区域码和下单端都已由 MetricRequest 严格校验，拼接进 SQL 是安全的。
    """

    def period(start, end):
        return f"o.order_time >= '{start}' AND o.order_time < '{end}'"

    current = period(request.start, request.end)
    base = period(request.base_start, request.base_end) if request.base_start else ""
    conditions = ["u.is_test=0", f"(({current}) OR ({base}))" if base else f"({current})"]
    if request.region_code:
        conditions.append(f"u.region_code='{request.region_code}'")
    if request.order_channel:
        conditions.append(f"o.channel='{request.order_channel}'")
    source = (
        f"FROM {order_table} o JOIN {user_table} u ON u.user_id=o.user_id WHERE "
        + " AND ".join(conditions)
    )

    def aggregates(condition, prefix):
        paid = f"({condition}) AND o.status IN ('paid','completed')"
        if definition.kind == "rate":
            return [
                f"SUM(CASE WHEN ({condition}) AND o.status='refunded' THEN 1 ELSE 0 END) AS {prefix}numerator",
                f"SUM(CASE WHEN ({condition}) AND o.status IN ('paid','completed','refunded') THEN 1 ELSE 0 END) AS {prefix}denominator",
            ]
        value = "o.pay_amount" if request.metric_id == "net_sales" else "1"
        return [f"SUM(CASE WHEN {paid} THEN {value} ELSE 0 END) AS {prefix}value"]

    expressions = (
        [*aggregates(base, "base_"), *aggregates(current, "current_")]
        if base
        else aggregates(current, "")
    )
    segment = {"overall": "'整体'", "region": "u.region_code", "order_channel": "o.channel"}[
        request.dimension
    ]
    detail_sql = (
        f"SELECT {segment} AS segment, "
        + ", ".join(expressions)
        + " "
        + source
        + " GROUP BY 1 ORDER BY segment"
    )
    total_sql = "SELECT " + ", ".join(expressions) + " " + source
    return detail_sql, total_sql, bool(base)


class QueryMetricTool(BaseTool):
    name = "query_metric"
    description = (
        "按固定口径查询或对比电商净销售额、有效订单数、退款率。先 get_metric_definition 确认 source=ecommerce_v1。"
        "程序固定 SQL、单位、状态、测试账号及关联；自动检查重复主键、缺失、日期、金额和覆盖边界。"
        "start/end 是当期，选填 base_start/base_end 对比；region 是用户注册区域，order_channel 是下单端。"
        "不得提交 SQL、公式或变更状态条件。上传表请先 profile_dataset，不能套用电商金额单位。"
    )

    def __init__(self, database: AnalyticsDatabase):
        super().__init__()
        self.database = database

    def schema_parameters(self):
        return MetricRequest.model_json_schema()

    async def _contract_tables(
        self, request: MetricRequest, definition: MetricDefinition
    ) -> tuple[str, str] | dict[str, Any]:
        """返回 (订单表, 用户表) 的引用名；契约不满足时返回要求澄清的结果。"""
        if self.database.dialect not in ("sqlite", "postgresql"):
            raise ToolError("固定指标当前只支持 SQLite 与 PostgreSQL")
        names = (
            ["business.orders", "business.users"]
            if self.database.dialect == "postgresql"
            else ["orders", "users"]
        )
        found, missing = await self.database.describe(names)
        if missing:
            return _gate_payload(definition, ["电商业务表未就绪；不能将同名上传表当作业务来源。"])
        orders, users = found
        required = [
            {"order_id", "user_id", "order_time", "status", "channel", "pay_amount"},
            {"user_id", "is_test", "region_code"},
        ]
        if any(
            not keys <= {column.name for column in table.columns}
            for keys, table in zip(required, found)
        ):
            return _gate_payload(
                definition, ["业务表字段与 ecommerce_v1 契约不一致，请核对来源映射。"]
            )
        amount = next(column for column in orders.columns if column.name == "pay_amount")
        cents_declared = re.search(r"单位[：:\s]*分(?:[；;，,。\s]|$)", amount.comment)
        numeric_type = any(token in amount.type.upper() for token in ("INT", "NUMERIC", "DECIMAL"))
        if request.metric_id == "net_sales" and (not cents_declared or not numeric_type):
            return _gate_payload(
                definition, ["pay_amount 未明确声明以分存储，禁止自动除以100；请核对数据契约。"]
            )
        return quoted_table(self.database, orders), quoted_table(self.database, users)

    async def execute(self, **kwargs):
        request = MetricRequest.model_validate(kwargs)
        definition = METRICS[request.metric_id]
        # 1. 请求本身缺条件时直接请用户澄清，不查库
        questions = _request_questions(request, definition)
        if questions:
            return _gate_payload(definition, questions)
        # 2. 业务表与 ecommerce_v1 契约一致（字段齐全、金额以分存储）才能计算
        tables = await self._contract_tables(request, definition)
        if isinstance(tables, dict):
            return tables
        order_table, user_table = tables
        # 3. 同一只读快照内依次执行：数据体检、分组统计、独立总量
        quality_sql = _quality_sql(self.database.dialect, order_table, user_table)
        quality = {}

        def validate_first(result):
            quality.update(row_dict(result))
            issues = []
            labels = {
                "duplicate_orders": "订单键重复",
                "duplicate_users": "用户键重复，关联会放大订单",
                "missing_orders": "订单关键字段缺失",
                "invalid_status": "未知订单状态",
                "invalid_dates": "下单时间不是有效标准时间",
                "invalid_users": "用户标识或测试账号标记无效",
                "orphan_orders": "订单无法关联到用户",
            }
            if request.metric_id == "net_sales":
                labels["invalid_money"] = "金额缺失、非数值、负数或不是整数分"
            for key, label in labels.items():
                if quality.get(key):
                    issues.append(f"{label}：{quality[key]} 条；请核对源数据。")
            if not quality["order_rows"]:
                issues.append("订单表为空，请确认导入或业务来源。")
            if not issues:
                issues.extend(
                    coverage_warnings(
                        quality["first_date"],
                        quality["last_date"],
                        min(filter(None, [request.start, request.base_start])),
                        max(request.end, request.base_end),
                    )
                )
            if issues:
                raise QualityGate(_gate_payload(definition, issues, quality))

        detail_sql, total_sql, has_base = _metric_sql(request, definition, order_table, user_table)
        try:
            results = await self.database.execute_snapshot(
                [quality_sql, detail_sql, total_sql], validate_first=validate_first
            )
        except QualityGate as exc:
            return exc.payload
        detail, total = results[1:]
        if detail.truncated or total.truncated:
            raise ToolError("指标分组结果被截断，拒绝生成不完整统计；请收窄筛选范围")
        if not detail.rows:
            return _gate_payload(
                definition,
                ["该时间和筛选范围未找到业务订单，不能将没有记录直接解释为业务零值。"],
                quality,
            )
        if request.metric_id == "net_sales":
            for result in (detail, total):
                for row in result.rows:
                    for index, column in enumerate(result.columns):
                        if column.endswith("value"):
                            row[index] = Decimal(str(row[index])) / 100
        warnings = [
            "检查范围覆盖全业务表；日期边界覆盖不证明数据完整，当前未接入业务入库完成水位。"
        ]
        # 4. 对比返回贡献拆解；单周期返回分组与总量
        if has_base:
            scope = AnalysisScope(
                metric=definition.name,
                metric_kind=definition.kind,
                source=SOURCE,
                unit=definition.unit,
                dimension={"overall": "整体", "region": "注册区域", "order_channel": "下单端"}[
                    request.dimension
                ],
                base_start=request.base_start,
                base_end=request.base_end,
                current_start=request.start,
                current_end=request.end,
                filters=[
                    "排除测试账号",
                    "按下单时间归属",
                    *(["注册区域=" + request.region_code] if request.region_code else []),
                    *(["下单端=" + request.order_channel] if request.order_channel else []),
                ],
            )
            comparison = compare_results(scope, detail, total)
            comparison["warnings"].extend(warnings)
            return {
                "status": "ready",
                "metric": definition.public(),
                "quality": quality,
                "comparison": comparison,
                "warnings": warnings,
                "queries": [detail_sql, total_sql],
            }
        values = row_dict(total)
        if definition.kind == "rate":
            denominator = values["denominator"]
            values["value"] = (
                Decimal(str(values["numerator"])) / Decimal(str(denominator)) * 100
                if denominator
                else None
            )
            if not denominator:
                warnings.append("已支付订单数为零，退款率未定义；不能写成0%。")
        groups = [dict(zip(detail.columns, row)) for row in detail.rows]
        # 总量始终独立计算；单周期展示只返回前8组及其余汇总，避免破坏工具 JSON。
        if len(groups) > 8:
            remainder = groups[8:]
            groups = [
                *groups[:8],
                {
                    "segment": "其余分组（合计）",
                    **{
                        column: sum(Decimal(str(row[column])) for row in remainder)
                        for column in detail.columns[1:]
                    },
                },
            ]
        return serialise(
            {
                "status": "ready",
                "metric": definition.public(),
                "quality": quality,
                "period": {"start": request.start, "end": request.end},
                "summary": values,
                "groups": groups,
                "group_count": len(detail.rows),
                "warnings": warnings,
                "queries": [detail_sql, total_sql],
            }
        )
