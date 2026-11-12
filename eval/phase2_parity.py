"""SQLite vs PostgreSQL gold SQL parity. No LLM calls or external data transfer."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.infrastructure.database.manage import ROOT, load_config
from eval.run_eval import gold_rows


def equivalent(left, right) -> bool:
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("0.000001")
    return left == right


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/ecommerce.db")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/phase2/parity.json")
    args = parser.parse_args()
    values = load_config()
    cases = json.loads((ROOT / "eval/questions.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        sqlite = gold_rows("sqlite:///" + args.source.resolve().as_posix(), case["gold_sql"])
        postgres = gold_rows(values["ANALYTICS_DB_URL"], case["gold_sql"])
        passed = len(sqlite) == len(postgres) and all(
            len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
            for a, b in zip(sqlite, postgres)
        )
        results.append({"id": case["id"], "passed": passed, "sqlite": sqlite, "postgres": postgres})
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "kind": "SQL parity (no LLM)",
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"SQL parity: {sum(r['passed'] for r in results)}/{len(results)}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
