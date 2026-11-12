"""第 13 课：执行标准 SQL、检查判分、阅读已有报告；不调用模型。"""
import json
import sqlite3
from contextlib import closing

from course_support import ROOT, demo_resources, prepare_output

from eval.run_eval import number_matches


def gold_rows(db_url, sql):
    # sqlite3 的事务上下文不会自动关闭连接；显式关闭，Windows 才能删除临时库。
    path = db_url[len("sqlite:///"):]
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        return conn.execute(sql).fetchall()


def main(db):
    cases = json.loads((ROOT / "eval/questions.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in cases}
    print("[1] 部分标准答案（真实执行 gold_sql）")
    for case_id in ["nov_net_sales", "dec_refund_rate", "category_mom_drop"]:
        print(case_id, gold_rows(db.url, by_id[case_id]["gold_sql"]))
    target = gold_rows(db.url, by_id["nov_net_sales"]["gold_sql"])[0][0]
    print("\n[2] 判分器实验")
    for answer in ["约 104.73 万元", "114.84 万元",
                   "错误候选是 1047258.23 元，我的结论是 1 元。"]:
        print(repr(answer), "=>", number_matches(answer, target, None, False))
    print("\n[3] 已有结果文件（历史记录，不是这次新跑的评测）")
    for filename in ["eval-results-full.json", "eval-results-noglossary.json"]:
        path = ROOT / filename
        if not path.exists():
            print(filename, "不存在，可在第 13 课的真模型实验中生成")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data:
            print(filename, "是空报告")
            continue
        print(filename, f"通过 {sum(bool(x['passed']) for x in data)}/{len(data)}",
              "平均记录 tokens", round(sum(x["tokens"] for x in data) / len(data)))
    print("注意：当前 tokens 和 llm_calls 不包含规划、反思模型的调用。")


if __name__ == "__main__":
    prepare_output()
    with demo_resources() as (db, _, __):
        main(db)
