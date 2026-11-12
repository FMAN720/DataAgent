"""
第 04 课答疑：run 方法开头为什么要 list(messages) 复制一份？

先看 Python 列表的「同一个列表，两个名字」现象，再模拟编排器调用 Agent，
对比「不复制」和「复制」两种写法的后果。不需要 API Key。

在 project-python 目录下运行：
    python lab/copy_demo.py
"""

from __future__ import annotations

import sys


def part1_alias() -> None:
    print("===== 实验 1：b = a 不是复制 =====")
    a = ["苹果", "香蕉"]
    b = a                      # 没有复制，只是给同一个列表多起了一个名字
    b.append("橙子")
    print(f"a = {a}")
    print(f"b = {b}")
    print(f"a 和 b 是同一个列表吗？ {a is b}\n")

    print("===== 实验 2：b = list(a) 才是复制 =====")
    a = ["苹果", "香蕉"]
    b = list(a)                # 新建一个列表，把 a 里的东西抄过去
    b.append("橙子")
    print(f"a = {a}")
    print(f"b = {b}")
    print(f"a 和 b 是同一个列表吗？ {a is b}\n")


def part2_function() -> None:
    print("===== 实验 3：把列表传给函数，函数改了它，外面也跟着变 =====")

    def add_fruit(basket):
        basket.append("西瓜")   # 函数拿到的就是外面那个列表本身

    my_basket = ["苹果"]
    add_fruit(my_basket)
    print(f"调用函数之后，my_basket = {my_basket}  ← 外面的列表被改了\n")


# ---------------------------------------------------------------- 实验 4、5
def agent_without_copy(messages):
    """不复制的写法：直接往传进来的列表里追加。"""
    convo = messages                                  # ← 没有复制
    convo.append({"role": "assistant", "content": "（点菜单）查口径"})
    convo.append({"role": "tool", "content": "（口径文档，上千字）……"})
    convo.append({"role": "assistant", "content": "11 月净销售额 1,047,258.23 元"})
    return convo[-1]["content"]


def agent_with_copy(messages):
    """项目里的写法：先复制一份，只改自己的副本。"""
    convo = list(messages)                            # ← 复制
    convo.append({"role": "assistant", "content": "（点菜单）查口径"})
    convo.append({"role": "tool", "content": "（口径文档，上千字）……"})
    convo.append({"role": "assistant", "content": "11 月净销售额 1,047,258.23 元"})
    return convo[-1]["content"]


def orchestrator(agent, label: str) -> None:
    """模拟编排器：拼好 messages 交给 Agent，拿回答案后，还要接着用 messages 做别的事。"""
    print(f"===== {label} =====")
    messages = [
        {"role": "system", "content": "你是数据分析师……"},
        {"role": "user", "content": "上个月销售额是多少？"},
    ]
    print(f"编排器：交给 Agent 前，messages 有 {len(messages)} 条")
    answer = agent(messages)
    print(f"编排器：拿到答案「{answer}」")
    print(f"编排器：Agent 用完之后，messages 有 {len(messages)} 条")
    for m in messages:
        print(f"    {m['role']:<9} {m['content']}")
    print()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    part1_alias()
    part2_function()
    orchestrator(agent_without_copy, "实验 4：Agent 不复制")
    orchestrator(agent_with_copy, "实验 5：Agent 先复制（项目里的写法）")
