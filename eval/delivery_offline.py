"""本机演示库的结果交付示例；无模型调用，--persist 才写入合成历史。"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from app.analysis.archive import AnalysisArchive
from app.analysis.artifacts import build_artifacts, confirmed_state
from app.analytics import AnalyticsDatabase
from app.analytics.evidence import analysis_summary
from app.analytics.metrics import QueryMetricTool
from app.analytics.tools import RunSQLTool
from app.config import Settings
from app.core.agent.orchestrator import AgentResponse
from app.core.agent.react_agent import ToolStep
from app.infrastructure.database.manage import load_config
from app.models.enums import AgentMode


async def main(out, persist):
    values = load_config()  # 只允许本项目专用本机库。
    database = AnalyticsDatabase(values["ANALYTICS_DB_URL"])
    archive = (
        AnalysisArchive.from_settings(Settings(_env_file=None, database_url=values["DATABASE_URL"]))
        if persist
        else None
    )
    try:
        query = QueryMetricTool(database)
        args = {
            "metric_id": "net_sales",
            "source": "ecommerce_v1",
            "base_start": "2025-11-01",
            "base_end": "2025-12-01",
            "start": "2025-12-01",
            "end": "2026-01-01",
            "dimension": "region",
        }
        result = await query.execute(**args)
        if result.get("comparison", {}).get("summary", {}).get("base") != "1047258.23":
            raise ValueError("本机演示库与预期不符，未自动创建演示历史")
        sql = (
            "SELECT substr(o.order_time,1,7) AS month, COUNT(*) AS order_count FROM business.orders o "
            "JOIN business.users u ON u.user_id=o.user_id WHERE u.is_test=0 AND o.status IN ('paid','completed') "
            "GROUP BY 1 ORDER BY 1"
        )
        trend = await RunSQLTool(database).execute(sql=sql)
        cases = [
            (
                "合成演示：11月至12月各区域净销售额对比",
                "query_metric",
                args,
                result,
                "合成演示，未调用聊天模型。11月净销售额 "
                + result["comparison"]["summary"]["base"]
                + " 元，12月为 "
                + result["comparison"]["summary"]["current"]
                + " 元。变化按用户注册区域拆解，不能据此证明业务原因。",
            ),
            (
                "合成演示：月度有效订单数趋势",
                "run_sql",
                {"sql": sql},
                trend,
                "合成演示，未调用聊天模型。折线来自演示库实际查询；本例统计每月有效订单数。图表不补造缺失观测，完整 SQL 和数据见查询证据。",
            ),
        ]
        output = {"model_api_calls": 0, "persisted": persist, "reports": []}
        for question, name, arguments, payload, answer in cases:
            sid = "delivery-demo-" + uuid.uuid4().hex
            step = ToolStep(
                1, name, arguments, True, json.dumps(payload, ensure_ascii=False, default=str), 0
            )
            response = AgentResponse(
                answer=answer,
                success=True,
                trace_id=str(uuid.uuid4()),
                session_id=sid,
                mode=AgentMode.REACT,
                question=question,
                steps=[step],
                analysis=analysis_summary([step]),
            )
            response.tables, response.charts = build_artifacts([step])
            snapshot = response.to_dict()
            if archive:
                snapshot = await archive.save(
                    snapshot, trusted=True, state=confirmed_state([step], {}), revision=0
                )
            output["reports"].append(snapshot)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "out": str(out),
                    "reports": len(cases),
                    "persisted": persist,
                    "model_api_calls": 0,
                },
                ensure_ascii=False,
            )
        )
    finally:
        database.dispose()
        if archive:
            await archive.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("reports/delivery-round2/demo.json"))
    parser.add_argument("--persist", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.out, arguments.persist))
