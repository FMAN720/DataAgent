"""
评测脚本：用真实模型跑评测集，按标准 SQL 的结果自动判分。

判分方式：执行 gold_sql 得到标准答案，检查 Agent 的最终回答文本中是否出现
对应的数字（允许误差）或文本。只看最终答案，不看中间 SQL，
因为同一个问题可以有多种正确写法。

用法（需要在 .env 或环境变量中配置 OPENAI_API_KEY）：
    python -m eval.run_eval
    python -m eval.run_eval --ids nov_net_sales dec_refund_rate
    python -m eval.run_eval --no-glossary      # 去掉口径检索工具做对比
    python -m eval.run_eval --no-reflection
    python -m eval.run_eval --mode plan_execute
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analytics.engine import AnalyticsDatabase, validate_readonly_sql  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import build_agent, configure_logging  # noqa: E402
from app.models.enums import AgentMode  # noqa: E402

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """从答案中提取数字，兼容千分位和「万」。"""
    out: list[float] = []
    for m in _NUMBER.finditer(text):
        raw = m.group(0).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.append(v)
        tail = text[m.end() : m.end() + 3].lstrip()[:1]
        if tail == "万":
            out.append(v * 10_000)
        elif tail == "亿":
            out.append(v * 100_000_000)
    return out


def number_matches(answer: str, expected: float, abs_tol: float | None, absolute: bool) -> bool:
    # 默认容差：相对 0.1%（允许「104.73 万」这种四舍五入写法）且至少 0.01
    tol = abs_tol if abs_tol is not None else max(0.01, abs(expected) * 0.001)
    target = abs(expected) if absolute else expected
    for v in extract_numbers(answer):
        if abs((abs(v) if absolute else v) - target) <= tol:
            return True
    return False


@dataclass
class CaseResult:
    id: str
    passed: bool
    failed_checks: list[str]
    answer: str
    tool_calls: int
    sql_errors: int
    llm_calls: int
    revised: bool
    tokens: int
    seconds: float


def gold_rows(db_url: str, sql: str, schemas=("business", "uploads")) -> list[tuple[Any, ...]]:
    """标准答案使用同一只读访问层，兼容 SQLite / PostgreSQL，关闭连接池。"""
    db = AnalyticsDatabase(db_url, max_rows=10_000, schemas=schemas)
    try:
        result = db._execute_sync(validate_readonly_sql(sql), threading.Event())
        if result.truncated:
            raise ValueError("标准答案超过 10000 行，请使用聚合 SQL")
        return [tuple(row) for row in result.rows]
    finally:
        db.dispose()


async def run_case(
    agent, case: dict[str, Any], db_url: str, mode: AgentMode, schemas=("business", "uploads")
) -> CaseResult:
    rows = await asyncio.to_thread(gold_rows, db_url, case["gold_sql"], schemas)
    t0 = time.perf_counter()
    resp = await agent.run(case["question"], mode=mode)
    seconds = time.perf_counter() - t0

    failed: list[str] = []
    for check in case["checks"]:
        r, c = check["cell"]
        expected = rows[r][c]
        if check["type"] == "number":
            ok = number_matches(
                resp.answer, float(expected), check.get("abs_tol"), check.get("absolute", False)
            )
        else:
            ok = str(expected) in resp.answer
        if not ok:
            failed.append(f"期望包含 {expected}")

    return CaseResult(
        id=case["id"],
        passed=resp.success and not failed,
        failed_checks=failed if resp.success else [f"运行失败: {resp.error}"],
        answer=resp.answer,
        tool_calls=len(resp.steps),
        sql_errors=sum(1 for s in resp.steps if s.tool == "run_sql" and not s.ok),
        llm_calls=resp.llm_calls,
        revised=resp.revised,
        tokens=resp.usage.get("total_tokens", 0),
        seconds=seconds,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="数据分析 Agent 评测")
    parser.add_argument("--ids", nargs="*", help="只跑指定 id 的题目")
    parser.add_argument(
        "--mode", choices=[m.value for m in AgentMode], default=AgentMode.REACT.value
    )
    parser.add_argument(
        "--no-glossary", action="store_true", help="移除 search_glossary 工具（对照实验）"
    )
    parser.add_argument("--no-reflection", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out", type=Path, help="把逐题结果写入 JSON 文件")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    if args.no_reflection:
        settings = settings.model_copy(update={"enable_reflection": False})
    agent = build_agent(
        settings, exclude_tools=frozenset({"search_glossary"}) if args.no_glossary else frozenset()
    )

    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    if args.ids:
        cases = [c for c in cases if c["id"] in set(args.ids)]

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(case):
        async with sem:
            return await run_case(
                agent,
                case,
                settings.analytics_db_url,
                AgentMode(args.mode),
                settings.analytics_db_schemas,
            )

    try:
        results: list[CaseResult] = await asyncio.gather(*(guarded(c) for c in cases))
    finally:
        await agent.aclose()
    if not results:
        print("没有匹配的评测题")
        return 1

    print(
        f"\n{'id':<24}{'结果':<6}{'工具':>5}{'SQL错':>6}{'LLM':>5}{'修正':>5}{'tokens':>8}{'秒':>7}"
    )
    for r in results:
        print(
            f"{r.id:<24}{'✓' if r.passed else '✗':<6}{r.tool_calls:>5}{r.sql_errors:>6}"
            f"{r.llm_calls:>5}{'是' if r.revised else '':>5}{r.tokens:>8}{r.seconds:>7.1f}"
        )
    for r in results:
        if not r.passed:
            print(f"\n✗ {r.id}: {'; '.join(r.failed_checks)}\n  答案: {r.answer[:300]}")

    n = len(results)
    passed = sum(r.passed for r in results)
    print(
        f"\n准确率 {passed}/{n} = {passed / n:.0%}  |  平均工具调用 {sum(r.tool_calls for r in results) / n:.1f}"
        f"  |  平均 tokens {sum(r.tokens for r in results) / n:.0f}  |  平均耗时 {sum(r.seconds for r in results) / n:.1f}s"
    )
    if args.out:
        args.out.write_text(
            json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if passed == n else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
