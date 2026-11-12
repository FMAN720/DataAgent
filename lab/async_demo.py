"""
第 05 课练习：async / await / asyncio.gather 到底是什么？

用「点外卖」模拟 Agent 一次点了三道菜（三个工具）：
每道菜要等 1 秒（模拟查数据库、等网络）。
  实验 1：一道一道等      → 总共约 3 秒
  实验 2：同时下单一起等  → 总共约 1 秒
  实验 3：gather 返回结果的顺序
  实验 4：其中一个出错时，gather 会怎样

不需要 API Key。在 project-python 目录下运行：
    python lab/async_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import time

START = 0.0


def now() -> str:
    """距离实验开始过了多少秒。"""
    return f"{time.perf_counter() - START:4.1f}s"


async def cook(dish: str, seconds: float) -> str:
    """模拟一个要等待的工具：查数据库、调接口等。"""
    print(f"  [{now()}] 开始做 {dish}")
    await asyncio.sleep(seconds)          # 等待期间，程序可以去做别的事
    print(f"  [{now()}] {dish} 做好了")
    return f"{dish}（用时 {seconds} 秒）"


async def broken_dish() -> str:
    print(f"  [{now()}] 开始做 糖醋鱼")
    await asyncio.sleep(0.5)
    raise RuntimeError("糖醋鱼烧糊了")


async def exp1_one_by_one() -> None:
    global START
    print("===== 实验 1：一道一道等（await 一个，再 await 下一个）=====")
    START = time.perf_counter()
    a = await cook("宫保鸡丁", 1)
    b = await cook("番茄炒蛋", 1)
    c = await cook("酸辣汤", 1)
    print(f"  拿到：{[a, b, c]}")
    print(f"  总耗时 {now()}\n")


async def exp2_gather() -> None:
    global START
    print("===== 实验 2：同时下单一起等（asyncio.gather）=====")
    START = time.perf_counter()
    results = await asyncio.gather(
        cook("宫保鸡丁", 1),
        cook("番茄炒蛋", 1),
        cook("酸辣汤", 1),
    )
    print(f"  拿到：{results}")
    print(f"  总耗时 {now()}\n")


async def exp3_order() -> None:
    global START
    print("===== 实验 3：做好的先后顺序不同，gather 返回的顺序呢？=====")
    START = time.perf_counter()
    results = await asyncio.gather(
        cook("慢菜：红烧肉", 1.5),
        cook("快菜：拍黄瓜", 0.3),
        cook("中等：青椒肉丝", 0.8),
    )
    print("  gather 返回的顺序：")
    for r in results:
        print(f"    {r}")
    print("  → 按「下单顺序」排，不是按「做好的顺序」排\n")


async def exp4_error() -> None:
    global START
    print("===== 实验 4：其中一道菜出错了 =====")
    START = time.perf_counter()
    try:
        await asyncio.gather(cook("宫保鸡丁", 1), broken_dish(), cook("酸辣汤", 1))
    except RuntimeError as exc:
        print(f"  [{now()}] gather 直接抛出了异常：{exc}，另外两道菜的结果一个都没拿到")
    print("  → 所以项目里每个工具都自己兜住异常，返回 ok=False，而不是往外抛（见第 05 课第 4 节）\n")


async def main() -> None:
    await exp1_one_by_one()
    await exp2_gather()
    await exp3_order()
    await exp4_error()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
