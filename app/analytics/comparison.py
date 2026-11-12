"""Period comparison computed from read-only SQL results, never model-supplied numbers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.analytics.engine import AnalyticsDatabase, QueryResult
from app.core.tools.base import BaseTool, ToolError


class AnalysisScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric: str = Field(default="", max_length=80)
    metric_kind: Literal["additive", "rate"] = "additive"
    source: str = Field(default="", max_length=120)
    unit: str = Field(default="", max_length=16)
    dimension: str = Field(default="", max_length=80)
    base_start: str = ""
    base_end: str = ""
    current_start: str = ""
    current_end: str = ""
    assumptions: list[str] = Field(default_factory=list, max_length=3)
    filters: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("metric", "source", "unit", "dimension")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("assumptions", "filters")
    @classmethod
    def short_assumptions(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 160 for value in values):
            raise ValueError("假设及筛选必须为非空且最多 160 字的说明")
        return values

    @field_validator("base_start", "base_end", "current_start", "current_end")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if value and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
            raise ValueError("日期使用 YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def ranges(self):
        for prefix in ("base", "current"):
            start, end = getattr(self, prefix + "_start"), getattr(self, prefix + "_end")
            if start and end and start >= end:
                raise ValueError("周期采用左闭右开区间，开始日必须早于结束日")
        if all((self.base_start, self.base_end, self.current_start, self.current_end)):
            if max(self.base_start, self.current_start) < min(self.base_end, self.current_end):
                raise ValueError("两个对比周期不能重叠")
        if self.metric_kind == "rate" and self.unit and self.unit not in ("%", "百分比"):
            raise ValueError("rate 仅用于分子不超过分母的比例，单位须为 % 或百分比")
        return self

    def questions(self) -> list[str]:
        questions = []
        if not self.metric:
            questions.append("要分析哪个指标？")
        if not self.source:
            questions.append("使用哪个业务库或已导入的数据集？")
        if not self.unit:
            questions.append("指标的单位是什么？请核对源字段，金额不能自动假定为分。")
        if not self.dimension:
            questions.append("先按哪个维度拆解，例如区域、下单渠道或品类？")
        if not self.base_start or not self.base_end:
            questions.append("作为比较基准的周期是什么？")
        if not self.current_start or not self.current_end:
            questions.append("需要分析的当前周期是什么？")
        return questions


def prepare_scope(scope: AnalysisScope) -> dict[str, Any]:
    questions = scope.questions()
    return {
        "status": "needs_clarification" if questions else "ready",
        "scope": scope.model_dump(),
        "questions": questions,
        "period_convention": "start_inclusive_end_exclusive",
    }


def number(value: Any) -> Decimal:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ToolError("查询数值必须为非空数字；缺失值不能自动当作 0，请在 SQL 中明确处理")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ToolError("查询数值无法解析") from exc
    if not result.is_finite() or abs(result) > Decimal("1e24"):
        raise ToolError("查询出现非有限或超出支持范围的数值")
    return result


def serialise(value: Any) -> Any:
    if isinstance(value, Decimal):
        # Preserve monetary precision without binary float conversion; cap repeating fractions.
        value = value.quantize(Decimal("0.00000001"))
        return format(value, "f").rstrip("0").rstrip(".") if value else "0"
    if isinstance(value, dict):
        return {key: serialise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialise(item) for item in value]
    return value


def _read(result: QueryResult, columns: list[str], *, grouped: bool) -> list[dict[str, Any]]:
    expected = ["segment", *columns] if grouped else columns
    if result.truncated:
        raise ToolError("分组或总量查询结果已截断，拒绝计算贡献；请缩小范围或维度粒度")
    if result.columns != expected:
        raise ToolError("查询必须按顺序返回列：" + ", ".join(expected))
    if not result.rows or (not grouped and len(result.rows) != 1):
        raise ToolError("分组查询至少需要一行，总量查询必须恰好一行；空数据不能冒充零值")
    rows, seen = [], set()
    for raw in result.rows:
        row = dict(zip(expected, raw))
        if grouped:
            segment = row["segment"]
            if segment is not None and not isinstance(segment, (str, int)):
                raise ToolError("segment 仅支持文本、整数或 NULL，请在 SQL 中明确转换")
            if isinstance(segment, bool) or len(str(segment)) > 128:
                raise ToolError("分组名称不能超过 128 字")
            segment_key = (type(segment).__name__, segment)
            if segment_key in seen:
                raise ToolError("同一 segment 出现多行，请先按完整维度聚合")
            seen.add(segment_key)
        for key in columns:
            row[key] = number(row[key])
        rows.append(row)
    return rows


def _rate(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator else None


def compare_results(
    scope: AnalysisScope, detail: QueryResult, total: QueryResult
) -> dict[str, Any]:
    if scope.questions():
        raise ToolError("请先补齐分析口径：" + "；".join(scope.questions()))
    columns = (
        ["base_value", "current_value"]
        if scope.metric_kind == "additive"
        else ["base_numerator", "base_denominator", "current_numerator", "current_denominator"]
    )
    with localcontext() as context:
        context.prec = 50
        rows = _read(detail, columns, grouped=True)
        totals = _read(total, columns, grouped=False)[0]
        if scope.metric_kind == "rate":
            for row in [*rows, totals]:
                for prefix in ("base", "current"):
                    n, d = row[prefix + "_numerator"], row[prefix + "_denominator"]
                    if n < 0 or d < 0 or n > d:
                        raise ToolError(
                            "比例的分子分母必须非负且分子不大于分母；不能将金额或客单价当作退款率"
                        )
        sums = {key: sum((row[key] for row in rows), Decimal(0)) for key in columns}
        differences = {key: sums[key] - totals[key] for key in columns}
        # Absolute tolerance only; a large aggregate must not hide a material mismatch.
        reconciled = all(abs(value) <= Decimal("0.000001") for value in differences.values())
        summary, groups = {}, []
        warnings = [
            "贡献是统计拆解，不能据此证明活动、价格或其他因素造成了变化。",
            "不同维度是同一变化的不同切面，不能把区域和渠道等拆解结果相加。",
        ]
        if (date.fromisoformat(scope.base_end) - date.fromisoformat(scope.base_start)).days != (
            date.fromisoformat(scope.current_end) - date.fromisoformat(scope.current_start)
        ).days:
            warnings.append("两个周期天数不同，当前比较未自动做日均调整。")
        if not reconciled:
            warnings.append(
                "分项与独立总量口径不一致，已停止计算贡献；请核对优惠分摊、JOIN 重复、过滤条件或缺失分组。"
            )
        if scope.metric_kind == "additive":
            base, current = totals["base_value"], totals["current_value"]
            delta = current - base
            summary = {
                "base": base,
                "current": current,
                "delta": delta,
                "change_pct": delta / base * 100 if base > 0 else None,
            }
            if base <= 0:
                warnings.append("基期为零或负值，增长率不定义；保留绝对变化。")
            for row in rows:
                change = row["current_value"] - row["base_value"]
                groups.append(
                    {
                        **row,
                        "delta": change,
                        "contribution_pct": change / delta * 100 if reconciled and delta else None,
                    }
                )
            if not delta:
                warnings.append("整体净变化为零，不定义贡献占比；分项可能存在相互抵消。")
        else:
            base = _rate(totals["base_numerator"], totals["base_denominator"])
            current = _rate(totals["current_numerator"], totals["current_denominator"])
            valid = base is not None and current is not None
            summary = {
                **totals,
                "base": base * 100 if base is not None else None,
                "current": current * 100 if current is not None else None,
                "delta_pp": (current - base) * 100
                if base is not None and current is not None
                else None,
            }
            if not valid:
                warnings.append("至少一个周期的总分母为零，比例和变化贡献无法完整定义。")
            for row in rows:
                b, c = (
                    _rate(row["base_numerator"], row["base_denominator"]),
                    _rate(row["current_numerator"], row["current_denominator"]),
                )
                within = mix = None
                if valid and reconciled:
                    # New/disappeared segments use the observed rate in both periods;
                    # their change is assigned to composition rather than an invented rate.
                    rb, rc = (
                        (b if b is not None else c or Decimal(0)),
                        (c if c is not None else b or Decimal(0)),
                    )
                    wb = row["base_denominator"] / totals["base_denominator"]
                    wc = row["current_denominator"] / totals["current_denominator"]
                    within = (rc - rb) * (wc + wb) / 2 * 100
                    mix = (wc - wb) * (rc + rb) / 2 * 100
                groups.append(
                    {
                        **row,
                        "base_rate": b * 100 if b is not None else None,
                        "current_rate": c * 100 if c is not None else None,
                        "within_pp": within,
                        "mix_pp": mix,
                        "contribution_pp": within + mix if within is not None else None,
                    }
                )
            warnings.append(
                "比例按总分子/总分母计算；组内变化和结构变化采用对称分解，单位为百分点。零分母分组的变化归入结构项。"
            )
        rank_key = "delta" if scope.metric_kind == "additive" else "contribution_pp"
        groups.sort(key=lambda row: abs(row.get(rank_key) or Decimal(0)), reverse=True)
        # Display a bounded table; all groups above participated in reconciliation.
        displayed, remainder = groups[:8], groups[8:]
        if remainder:
            other: dict[str, Any] = {"segment": "其余分组（合计）", "is_other": True}
            for key in columns:
                other[key] = sum((row[key] for row in remainder), Decimal(0))
            if scope.metric_kind == "additive":
                other["delta"] = sum(row["delta"] for row in remainder)
                other["contribution_pct"] = (
                    other["delta"] / summary["delta"] * 100
                    if reconciled and summary["delta"]
                    else None
                )
            else:
                for key in ("within_pp", "mix_pp", "contribution_pp"):
                    other[key] = (
                        sum(row[key] for row in remainder)
                        if all(row[key] is not None for row in remainder)
                        else None
                    )
                for prefix in ("base", "current"):
                    rate = _rate(other[prefix + "_numerator"], other[prefix + "_denominator"])
                    other[prefix + "_rate"] = rate * 100 if rate is not None else None
            displayed.append(other)
        return serialise(
            {
                "status": "ready" if reconciled else "scope_mismatch",
                "scope": scope.model_dump(),
                "summary": summary,
                "groups": displayed,
                "group_count": len(groups),
                "reconciliation": {
                    "matched": reconciled,
                    "group_totals": sums,
                    "differences": differences,
                },
                "warnings": warnings,
                "causal_claim_supported": False,
            }
        )


class PrepareAnalysisTool(BaseTool):
    name = "prepare_analysis"
    description = (
        "比较、归因或下钻前确认指标、来源、单位、维度和两个周期。开始日包含、结束日不包含。"
        "只比较整体时 dimension 填整体。先查表结构和业务口径；能据上下文确定的条件直接填入并记录假设，不能猜的字段留空。缺少条件时停止并请用户补充。"
    )

    def schema_parameters(self):
        return {
            "type": "object",
            "properties": {"scope": AnalysisScope.model_json_schema()},
            "required": ["scope"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs):
        return prepare_scope(AnalysisScope.model_validate(kwargs.get("scope", {})))


class ComparePeriodsTool(BaseTool):
    name = "compare_periods"
    description = (
        "在同一只读快照执行分组 SQL 与独立总量 SQL，计算变化并核对可加性。"
        "additive: 分组列按 segment, base_value, current_value；总量列 base_value, current_value。"
        "rate: 分组列 segment, base_numerator, base_denominator, current_numerator, current_denominator；总量去掉 segment。"
        "两周期使用条件聚合，包含新增和消失分组，不加 LIMIT；不得用明细金额直接解释订单实付金额。"
        "核对不通过时禁止报告贡献。需要继续下钻时收窄两个 SQL 的同一父级过滤条件，保持同一口径。"
    )

    def __init__(self, database: AnalyticsDatabase):
        super().__init__()
        self.database = database

    def schema_parameters(self):
        return {
            "type": "object",
            "properties": {
                "scope": AnalysisScope.model_json_schema(),
                "detail_sql": {"type": "string", "description": "单个维度的分组只读查询"},
                "total_sql": {
                    "type": "string",
                    "description": "同范围同口径的独立总量只读查询，不从分组结果求和冒充核对",
                },
            },
            "required": ["scope", "detail_sql", "total_sql"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs):
        scope = AnalysisScope.model_validate(kwargs.get("scope", {}))
        if scope.questions():
            raise ToolError("请先补齐分析口径：" + "；".join(scope.questions()))
        results = await self.database.execute_many(
            [kwargs.get("detail_sql"), kwargs.get("total_sql")]
        )
        return compare_results(scope, *results)
