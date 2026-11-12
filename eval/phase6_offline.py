"""Offline integration examples and optional localhost UI preview. No model API calls.

python -m eval.phase6_offline
python -m eval.phase6_offline --serve --port 18081
All answers/reviews are scripted, not a measurement of real model answer quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.core.tools.registry import ToolRegistry
from app.infrastructure.llm.model_router import LLMResponse, ToolCall

ROOT = Path(__file__).resolve().parents[1]
SCOPE = {
    "metric": "净销售额",
    "metric_kind": "additive",
    "source": "合成电商演示库（离线验收）",
    "unit": "元",
    "dimension": "区域",
    "base_start": "2025-11-01",
    "base_end": "2025-12-01",
    "current_start": "2025-12-01",
    "current_end": "2026-01-01",
    "filters": ["排除测试账号", "有效订单 paid/completed", "按下单时间归属"],
    "assumptions": [],
}
COMMON = "u.is_test=0 AND o.order_time >= '2025-11-01' AND o.order_time < '2026-01-01'"
NET = "o.status IN ('paid','completed') AND " + COMMON


def expressions(field):
    return (
        f"SUM(CASE WHEN o.order_time < '2025-12-01' THEN {field} ELSE 0 END) / 100.0 AS base_value, "
        f"SUM(CASE WHEN o.order_time >= '2025-12-01' THEN {field} ELSE 0 END) / 100.0 AS current_value"
    )


def query_pair(kind):
    total = f"SELECT {expressions('o.pay_amount')} FROM orders o JOIN users u ON u.user_id=o.user_id WHERE {NET}"
    if kind == "mismatch":
        detail = f"""SELECT c.category_name AS segment, {expressions("i.item_amount")}
            FROM orders o JOIN users u ON u.user_id=o.user_id JOIN order_items i ON i.order_id=o.order_id
            JOIN products p ON p.product_id=i.product_id JOIN categories c ON c.category_id=p.category_id
            WHERE {NET} GROUP BY c.category_name"""
    elif kind == "rate":
        fields = ", ".join(
            f"SUM(CASE WHEN o.order_time {operator} '2025-12-01' {condition} THEN 1 ELSE 0 END) AS {prefix}_{measure}"
            for prefix, operator in (("base", "<"), ("current", ">="))
            for measure, condition in (
                ("numerator", "AND o.status='refunded'"),
                ("denominator", ""),
            )
        )
        source = f"FROM orders o JOIN users u ON u.user_id=o.user_id WHERE o.status IN ('paid','completed','refunded') AND {COMMON}"
        total = f"SELECT {fields} {source}"
        detail = f"SELECT o.channel AS segment, {fields} {source} GROUP BY o.channel"
    else:
        detail = f"SELECT r.region_name AS segment, {expressions('o.pay_amount')} FROM orders o JOIN users u ON u.user_id=o.user_id JOIN regions r ON r.region_code=u.region_code WHERE {NET} GROUP BY r.region_name"
    return detail, total


class OfflineLLM:
    """Scripted control flow; no clients, credentials, network or arbitrary input files."""

    async def chat(self, messages, **kwargs):
        if kwargs.get("response_format"):
            return LLMResponse(
                content=json.dumps(
                    {
                        "passed": True,
                        "quality_score": 95,
                        "summary": "离线脚本按工具结果返回审查结构；不代表真实模型的判断能力",
                        "checks": [
                            {
                                "claim": "变化数字或口径不一致来自本轮 compare_periods 输出",
                                "status": "supported",
                                "evidence_ids": ["E2"],
                                "reason": "核对只读查询计算的 summary 和 reconciliation；因果原因尚未验证",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        last_user = max(
            index for index, message in enumerate(messages) if message["role"] == "user"
        )
        question = messages[last_user]["content"]
        tools = [message for message in messages[last_user + 1 :] if message["role"] == "tool"]
        kind = "mismatch" if "口径冲突" in question else "rate" if "退款率" in question else "net"
        scope = dict(SCOPE)
        if kind == "rate":
            scope.update(
                metric="退款率",
                metric_kind="rate",
                unit="%",
                dimension="下单渠道",
                filters=["排除测试账号", "已支付订单 paid/completed/refunded", "按下单时间归属"],
            )
        if kind == "mismatch":
            scope["dimension"] = "品类（与净销售额口径核对）"
        if not tools:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="scope",
                        name="prepare_analysis",
                        arguments=json.dumps(
                            {"scope": {"metric": "销售额"} if "缺少条件" in question else scope},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )
        if len(tools) == 1:
            detail, total = query_pair(kind)
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="comparison",
                        name="compare_periods",
                        arguments=json.dumps(
                            {"scope": scope, "detail_sql": detail, "total_sql": total},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )
        comparison = json.loads(tools[-1]["content"])
        if comparison.get("status") == "scope_mismatch":
            answer = "分项与独立总量不一致，无法报告净销售额的品类贡献。请先核对明细金额与订单实付金额的优惠口径。"
        else:
            summary = comparison["summary"]
            change = summary["delta_pp"] if kind == "rate" else summary["delta"]
            unit = "个百分点" if kind == "rate" else "元"
            answer = f"{scope['metric']}从 {summary['base']} 变为 {summary['current']}，变化 {change} {unit}。分项见下表。\n这些数字说明变化分布，原因尚未验证；需要同期活动执行、流量与转化等证据。"
        return LLMResponse(
            content="【离线合成演示，回答与审查由脚本生成，未调用模型 API】\n" + answer
        )


def build_offline_agent():
    db = AnalyticsDatabase("sqlite:///" + (ROOT / "data/ecommerce.db").as_posix())
    registry = ToolRegistry()
    for tool in build_analytics_tools(db, GlossaryStore.from_file(ROOT / "app/demo/glossary.md")):
        registry.register(tool)

    async def close():
        db.dispose()

    async def ready():
        await db.execute("SELECT 1")

    return AgentOrchestrator(
        OfflineLLM(),
        registry,
        InMemorySessionStore(),
        system_prompt="离线合成演示",
        close_callback=close,
        readiness_check=ready,
    )


async def evaluate(out):
    cases = [
        ("additive", "比较 11 月和 12 月净销售额，按区域拆解", "reviewed"),
        ("rate", "比较退款率，按下单渠道拆解", "reviewed"),
        ("scope_mismatch", "检查品类与净销售额的口径冲突", "reviewed"),
        ("clarification", "缺少条件：销售额为什么变化", "needs_clarification"),
    ]
    results = []
    for case_id, question, status in cases:
        instance = build_offline_agent()
        try:
            result = await instance.run(question)
            passed = result.success and result.analysis["status"] == status
            if case_id == "scope_mismatch":
                passed = passed and result.analysis["comparisons"][0]["status"] == "scope_mismatch"
            results.append(
                {
                    "id": case_id,
                    "question": question,
                    "passed": passed,
                    "answer": result.answer,
                    "analysis": result.analysis,
                    "reflection": result.reflection.to_dict() if result.reflection else None,
                    "steps": [step.to_dict() for step in result.steps],
                }
            )
        finally:
            await instance.aclose()
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "scripted offline integration, NOT real model answer evaluation",
        "external_api_requests": 0,
        "results": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Offline integration: {sum(result['passed'] for result in results)}/{len(results)}; external API requests: 0"
    )
    return 0 if all(result["passed"] for result in results) else 1


def preview_app():
    from app.config import Settings, get_settings
    from app.main import create_app

    app = create_app()
    settings = Settings(
        _env_file=None, documents_enabled=False, datasets_enabled=False, embedding_enabled=False
    )
    app.dependency_overrides[get_settings] = lambda: settings

    @asynccontextmanager
    async def lifespan(app):
        instance = build_offline_agent()

        class EmptyDatasets:
            async def list(self, **kwargs):
                return []

        app.state.agent, app.state.dataset_service = instance, EmptyDatasets()
        app.state.document_session_factory = None
        app.state.document_error = "离线演示不读取用户资料"
        try:
            yield
        finally:
            await instance.aclose()

    app.router.lifespan_context = lifespan
    return app


def main():
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "reports/phase6/offline-analysis.json")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    if args.serve:
        import uvicorn

        uvicorn.run(preview_app(), host="127.0.0.1", port=args.port)
        return 0
    return asyncio.run(evaluate(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
