"""SQL 只读安全：应用层校验 + 数据库层只读，两层都要单独生效。"""

from __future__ import annotations

import threading

import pytest

from app.analytics.engine import SQLValidationError, validate_readonly_sql
from tests.conftest import run


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT created_at, updated_at FROM t",  # 字段名含 create / update，不能误杀
        "select * from refunds where reason = 'delete'",  # 字符串里的关键字不算
        "WITH m AS (SELECT 1 AS x) SELECT * FROM m;",  # 结尾分号允许
        "select replace(name, 'a', 'b') from t",  # replace 是函数
        "-- 注释里写 drop table\nselect 1",
    ],
)
def test_allows_legit_selects(sql: str) -> None:
    assert validate_readonly_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "update orders set status = 'paid'",
        "select 1; drop table orders",
        "select * into backup from orders",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE 'x.db' AS x",
        "with x as (delete from orders returning *) select * from x",
        "",
    ],
)
def test_rejects_writes_and_multi_statements(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_readonly_sql(sql)


def test_connection_is_readonly_even_if_validator_is_bypassed(db) -> None:
    """直接调用底层执行函数（绕过校验），数据库层仍然拒绝写入。"""
    with pytest.raises(Exception, match="readonly"):
        db._execute_sync("DELETE FROM orders", threading.Event())
    result = run(db.execute("SELECT COUNT(*) FROM orders"))
    assert result.rows[0][0] > 9000


def test_row_limit_truncates(db) -> None:
    result = run(db.execute("SELECT order_id FROM orders"))
    assert len(result.rows) == db.max_rows
    assert result.truncated


def test_sql_error_message_is_readable(db) -> None:
    with pytest.raises(SQLValidationError, match="no such column"):
        run(db.execute("SELECT not_a_column FROM orders"))
