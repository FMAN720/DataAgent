"""评测集本身的正确性：gold SQL 能执行、判分函数行为符合预期。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from eval.run_eval import extract_numbers, number_matches
from tests.conftest import PROJECT_ROOT

CASES = json.loads((PROJECT_ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_gold_sql_runs_and_check_cells_exist(case, demo_db_path) -> None:
    with sqlite3.connect(demo_db_path) as conn:
        rows = conn.execute(case["gold_sql"]).fetchall()
    for check in case["checks"]:
        r, c = check["cell"]
        assert rows[r][c] is not None


def test_known_answers_are_stable(demo_db_path) -> None:
    """演示数据用固定种子生成，关键数字应当可复现（改动 seed.py 后需要同步更新 README）。"""
    by_id = {c["id"]: c for c in CASES}
    with sqlite3.connect(demo_db_path) as conn:
        assert conn.execute(by_id["nov_net_sales"]["gold_sql"]).fetchone()[0] == 1047258.23
        assert conn.execute(by_id["category_mom_drop"]["gold_sql"]).fetchone() == (
            "家用电器",
            -64.0,
        )


def test_extract_numbers_handles_commas_and_wan() -> None:
    nums = extract_numbers("净销售额为 1,047,258.23 元，约 104.73万元，退款率 8.0%")
    assert 1047258.23 in nums
    assert pytest.approx(1047300.0) in nums
    assert 8.0 in nums


def test_number_matching_tolerance() -> None:
    assert number_matches("约 104.73 万元", 1047258.23, None, False)
    assert not number_matches("约 114.84 万元", 1047258.23, None, False)
    assert number_matches("下降了 64.0%", -64.0, 0.6, True)
    assert number_matches("退款率为 8.0%", 8.0, 0.15, False)
    assert not number_matches("退款率为 9.2%", 8.0, 0.15, False)
