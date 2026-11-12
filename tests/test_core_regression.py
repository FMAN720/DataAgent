from __future__ import annotations

import pytest

from eval.core_fixtures import build_core_database
from eval.core_regression import load_cases, run_case
from tests.conftest import run


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["id"])
def test_cross_domain_tool_and_workflow_regression(tmp_path, case):
    path = tmp_path / "synthetic.db"
    build_core_database(path)
    result = run(run_case(case, path, tmp_path / "metadata" / "workspace.sqlite3"))
    assert result["passed"], result
