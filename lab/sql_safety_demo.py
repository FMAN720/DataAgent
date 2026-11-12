"""第 07 课：校验、数据库只读、截断、超时；全部离线。"""
import threading

from course_support import demo_resources, prepare_output, run

from app.analytics.engine import SQLValidationError, validate_readonly_sql


async def main(db):
    print("[1] 应用层校验（这里只校验字符串，不执行）")
    for sql in ["SELECT created_at FROM t", "SELECT 'delete;drop' AS note",
                "WITH x AS (SELECT 1) SELECT * FROM x", "DELETE FROM orders",
                "SELECT 1; DROP TABLE orders"]:
        try:
            print("允许:", validate_readonly_sql(sql))
        except SQLValidationError as exc:
            print("拒绝:", sql, "=>", exc)
    print("\n[2] 直接绕过校验，仅在临时演示库测试")
    before = (await db.execute("SELECT COUNT(*) FROM orders")).rows[0][0]
    try:
        db._execute_sync("DELETE FROM orders", threading.Event())
    except Exception as exc:
        print("数据库拒绝写入:", str(exc).splitlines()[0])
    after = (await db.execute("SELECT COUNT(*) FROM orders")).rows[0][0]
    assert before == after
    print("订单数量前后相同:", before, after)
    print("\n[3] 行数上限")
    result = await db.execute("SELECT order_id FROM orders ORDER BY order_id")
    print("返回行数:", len(result.rows), "truncated:", result.truncated)
    assert len(result.rows) == 3 and result.truncated
    print("\n[4] 超时取消：递归构造大量数字")
    db.timeout_seconds = 0.05
    try:
        await db.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL "
                         "SELECT x+1 FROM n WHERE x<100000000) SELECT SUM(x) FROM n")
    except SQLValidationError as exc:
        assert "被中止" in str(exc)
        print(exc)
    else:
        raise AssertionError("递归查询未超时，请调高递归上限")
    # wait_for 不会强杀线程；线程会在下一次 progress handler 中看到取消标志。
    db.timeout_seconds = 5
    print("超时后仍能查询:", (await db.execute("SELECT 1")).rows)


if __name__ == "__main__":
    prepare_output()
    with demo_resources(max_rows=3) as (db, _, __):
        run(main(db))
