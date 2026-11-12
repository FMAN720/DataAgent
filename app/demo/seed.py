"""
生成演示用电商分析库（SQLite）。

数据用固定随机种子生成，每次结果完全一致，评测集的标准答案因此可复现。
刻意埋了几处「不看口径就会算错」的地方：
- 订单有 cancelled / refunded 状态，GMV 与净销售额口径不同；
- 存在测试账号（is_test=1），统计时需排除；
- 金额字段单位是「分」，展示要换算成「元」；
- 有两张名字相近的表：refunds（已发生退款）与 refund_requests（退款申请，可能被驳回）；
- region 存的是英文编码（EAST / NORTH ...），用户问的是「华东」「华北」。
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from loguru import logger

SEED = 20251231
YEAR = 2025

SCHEMA_SQL = """
CREATE TABLE regions (
    region_code TEXT PRIMARY KEY,           -- 区域编码：EAST / NORTH / SOUTH / CENTRAL / WEST
    region_name TEXT NOT NULL               -- 中文名：华东 / 华北 / 华南 / 华中 / 西部
);

CREATE TABLE categories (
    category_id   INTEGER PRIMARY KEY,
    category_name TEXT NOT NULL,            -- 一级品类名称
    parent_id     INTEGER                   -- 预留，当前均为 NULL（无二级品类）
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(category_id),
    list_price   INTEGER NOT NULL,          -- 标价，单位：分
    cost_price   INTEGER NOT NULL,          -- 成本价，单位：分
    is_active    INTEGER NOT NULL DEFAULT 1 -- 1 在售，0 下架
);

CREATE TABLE users (
    user_id         INTEGER PRIMARY KEY,
    register_date   TEXT NOT NULL,          -- 注册日期 YYYY-MM-DD
    region_code     TEXT NOT NULL REFERENCES regions(region_code),
    gender          TEXT,                   -- M / F / NULL（未填写）
    birth_year      INTEGER,
    member_level    TEXT NOT NULL,          -- normal / silver / gold
    acquire_channel TEXT NOT NULL,          -- 拉新渠道：organic / ads / referral
    is_test         INTEGER NOT NULL DEFAULT 0  -- 1 表示内部测试账号，统计时必须排除
);

CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    order_time      TEXT NOT NULL,          -- 下单时间 YYYY-MM-DD HH:MM:SS
    status          TEXT NOT NULL,          -- paid 已支付 / completed 已完成 / cancelled 已取消（未支付） / refunded 已全额退款
    channel         TEXT NOT NULL,          -- 下单端：app / web / mini_program
    total_amount    INTEGER NOT NULL,       -- 商品原价合计，单位：分
    discount_amount INTEGER NOT NULL,       -- 优惠金额，单位：分
    pay_amount      INTEGER NOT NULL,       -- 实付金额 = total_amount - discount_amount，单位：分；cancelled 订单为 0
    pay_time        TEXT                    -- 支付时间；cancelled 为 NULL
);

CREATE TABLE order_items (
    item_id     INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(order_id),
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    quantity    INTEGER NOT NULL,
    unit_price  INTEGER NOT NULL,           -- 成交单价（未分摊优惠），单位：分
    item_amount INTEGER NOT NULL            -- quantity * unit_price，单位：分
);

CREATE TABLE payments (
    payment_id  INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(order_id),
    pay_method  TEXT NOT NULL,              -- alipay / wechat / card
    amount      INTEGER NOT NULL,           -- 单位：分
    paid_at     TEXT NOT NULL
);

CREATE TABLE refunds (
    refund_id     INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    refund_amount INTEGER NOT NULL,         -- 实际退款金额，单位：分
    refund_time   TEXT NOT NULL,
    reason        TEXT NOT NULL             -- quality / not_needed / late_delivery / other
);

CREATE TABLE refund_requests (
    request_id   INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES orders(order_id),
    request_time TEXT NOT NULL,
    status       TEXT NOT NULL,             -- approved 已通过（会产生 refunds 记录） / rejected 已驳回
    reason       TEXT NOT NULL
);

CREATE TABLE user_events (
    event_id   INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    event_date TEXT NOT NULL,               -- YYYY-MM-DD
    event_type TEXT NOT NULL                -- login 登录 / view 浏览商品 / add_cart 加购
);

CREATE INDEX idx_orders_time ON orders(order_time);
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_events_date ON user_events(event_date);
"""

REGIONS = [
    ("EAST", "华东", 0.34),
    ("NORTH", "华北", 0.22),
    ("SOUTH", "华南", 0.24),
    ("CENTRAL", "华中", 0.12),
    ("WEST", "西部", 0.08),
]

# (品类名, [(商品名, 标价元)])
CATALOG = [
    (
        "手机数码",
        [
            ("旗舰手机", 4999),
            ("降噪耳机", 899),
            ("智能手表", 1299),
            ("移动电源", 129),
            ("平板电脑", 2999),
        ],
    ),
    ("家用电器", [("扫地机器人", 1999), ("空气净化器", 1299), ("电饭煲", 399), ("电动牙刷", 199)]),
    (
        "服饰鞋包",
        [("羽绒服", 699), ("运动鞋", 459), ("双肩包", 239), ("纯棉T恤", 79), ("牛仔裤", 199)],
    ),
    ("美妆个护", [("精华液", 459), ("面膜套装", 129), ("洗发水", 69), ("防晒霜", 119)]),
    (
        "食品生鲜",
        [("坚果礼盒", 159), ("进口牛奶", 89), ("咖啡豆", 99), ("车厘子", 199), ("大米", 59)],
    ),
    ("图书文娱", [("技术图书", 89), ("儿童绘本", 59), ("桌游", 149)]),
    ("家居日用", [("四件套", 299), ("收纳箱", 49), ("保温杯", 129), ("香薰", 79)]),
]

# 每月订单量的季节系数：11 月大促，2 月春节淡季
MONTH_FACTOR = {
    1: 0.9,
    2: 0.7,
    3: 0.95,
    4: 1.0,
    5: 1.0,
    6: 1.25,
    7: 0.95,
    8: 0.95,
    9: 1.0,
    10: 1.1,
    11: 1.8,
    12: 1.2,
}

# 品类月度系数：制造「环比下降最多」等有明确答案的问题
CATEGORY_MONTH_FACTOR = {
    ("服饰鞋包", 11): 1.3,
    ("服饰鞋包", 12): 1.25,
    ("家用电器", 12): 0.55,  # 12 月家电在大促后明显回落
    ("食品生鲜", 12): 1.4,  # 年货
    ("手机数码", 9): 1.5,  # 新机发布
}

N_USERS = 1200
N_TEST_USERS = 15
N_ORDERS = 9000


def _rand_time(rng: random.Random, d: date) -> str:
    h = rng.choices(
        range(24),
        weights=[1, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 7, 6, 6, 6, 6, 6, 7, 9, 10, 10, 7, 3],
    )[0]
    return datetime(d.year, d.month, d.day, h, rng.randint(0, 59), rng.randint(0, 59)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _days_in_month(month: int) -> int:
    nxt = date(YEAR + (month == 12), month % 12 + 1, 1)
    return (nxt - date(YEAR, month, 1)).days


def build_demo_db(path: Path) -> None:
    """（重新）生成演示库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()

    rng = random.Random(SEED)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.executemany("INSERT INTO regions VALUES (?, ?)", [(c, n) for c, n, _ in REGIONS])

        products: list[
            tuple[int, int, str, int]
        ] = []  # (product_id, category_id, category_name, price_fen)
        pid = 1
        for cid, (cname, items) in enumerate(CATALOG, start=1):
            conn.execute("INSERT INTO categories VALUES (?, ?, NULL)", (cid, cname))
            for pname, price_yuan in items:
                price = price_yuan * 100
                cost = int(price * rng.uniform(0.45, 0.75))
                active = 0 if pid in (4, 22) else 1
                conn.execute(
                    "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)",
                    (pid, pname, cid, price, cost, active),
                )
                products.append((pid, cid, cname, price))
                pid += 1

        # ---- 用户 ----
        region_codes = [c for c, _, _ in REGIONS]
        region_weights = [w for _, _, w in REGIONS]
        users: list[tuple[int, str]] = []  # (user_id, register_date)
        start_reg = date(2023, 1, 1)
        for uid in range(1, N_USERS + N_TEST_USERS + 1):
            is_test = 1 if uid > N_USERS else 0
            # 注册日期：2023-2025，越近越多
            offset = int((rng.random() ** 0.6) * ((date(YEAR, 12, 31) - start_reg).days))
            reg_date = start_reg + timedelta(days=offset)
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    reg_date.isoformat(),
                    rng.choices(region_codes, region_weights)[0],
                    rng.choice(["M", "F", "F", None]),
                    rng.randint(1965, 2006),
                    rng.choices(["normal", "silver", "gold"], [0.7, 0.2, 0.1])[0],
                    rng.choices(["organic", "ads", "referral"], [0.5, 0.35, 0.15])[0],
                    is_test,
                ),
            )
            users.append((uid, reg_date.isoformat()))

        real_users = [u for u in users if u[0] <= N_USERS]
        test_users = [u for u in users if u[0] > N_USERS]
        # 用户活跃度服从长尾：少数用户贡献大量订单
        user_weights = [rng.paretovariate(1.6) for _ in real_users]

        # ---- 订单 ----
        month_total = sum(MONTH_FACTOR[m] * _days_in_month(m) for m in range(1, 13))
        order_id = 1
        item_id = 1
        payment_id = 1
        refund_id = 1
        request_id = 1
        for month in range(1, 13):
            n_month = round(N_ORDERS * MONTH_FACTOR[month] * _days_in_month(month) / month_total)
            # 测试账号每月固定刷一批大额订单，不排除会显著拉高 GMV
            n_test = 12
            for k in range(n_month + n_test):
                is_test_order = k >= n_month
                day = rng.randint(1, _days_in_month(month))
                d = date(YEAR, month, day)
                if is_test_order:
                    uid = rng.choice(test_users)[0]
                else:
                    while True:
                        uid, reg = rng.choices(real_users, user_weights)[0]
                        if reg <= d.isoformat():
                            break
                otime = _rand_time(rng, d)

                n_items = rng.choices([1, 2, 3, 4], [0.55, 0.28, 0.12, 0.05])[0]
                chosen: list[tuple[int, int, str, int]] = []
                for _ in range(n_items):
                    # 按品类月度系数加权挑选商品
                    weights = [CATEGORY_MONTH_FACTOR.get((p[2], month), 1.0) for p in products]
                    chosen.append(rng.choices(products, weights)[0])

                lines = []
                total = 0
                for p_id, _, _, price in chosen:
                    qty = rng.choices([1, 2, 3], [0.8, 0.15, 0.05])[0]
                    unit = int(price * rng.choice([1.0, 1.0, 0.95, 0.9]))
                    if is_test_order:
                        qty = 5
                    amt = qty * unit
                    lines.append((p_id, qty, unit, amt))
                    total += amt

                discount = 0
                if month == 11:
                    discount = int(total * rng.uniform(0.08, 0.2))
                elif rng.random() < 0.3:
                    discount = int(total * rng.uniform(0.02, 0.1))

                r = rng.random()
                if is_test_order:
                    status = "completed"
                elif r < 0.07:
                    status = "cancelled"
                elif r < 0.07 + (0.09 if month in (11, 12) else 0.05):
                    status = "refunded"
                elif d >= date(YEAR, 12, 20):
                    status = "paid"
                else:
                    status = "completed"

                pay_amount = 0 if status == "cancelled" else total - discount
                pay_time: str | None = None
                if status != "cancelled":
                    pt = datetime.strptime(otime, "%Y-%m-%d %H:%M:%S") + timedelta(
                        minutes=rng.randint(1, 30)
                    )
                    pay_time = pt.strftime("%Y-%m-%d %H:%M:%S")

                conn.execute(
                    "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        order_id,
                        uid,
                        otime,
                        status,
                        rng.choices(["app", "web", "mini_program"], [0.6, 0.15, 0.25])[0],
                        total,
                        discount,
                        pay_amount,
                        pay_time,
                    ),
                )
                for p_id, qty, unit, amt in lines:
                    conn.execute(
                        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)",
                        (item_id, order_id, p_id, qty, unit, amt),
                    )
                    item_id += 1

                if pay_time:
                    conn.execute(
                        "INSERT INTO payments VALUES (?, ?, ?, ?, ?)",
                        (
                            payment_id,
                            order_id,
                            rng.choices(["alipay", "wechat", "card"], [0.45, 0.45, 0.1])[0],
                            pay_amount,
                            pay_time,
                        ),
                    )
                    payment_id += 1

                # 退款与被驳回的申请只出现在已支付订单上
                assert pay_time is not None or status not in ("refunded", "completed")
                reason = rng.choices(
                    ["quality", "not_needed", "late_delivery", "other"], [0.35, 0.35, 0.2, 0.1]
                )[0]
                if status == "refunded":
                    rt = datetime.strptime(pay_time or "", "%Y-%m-%d %H:%M:%S") + timedelta(
                        days=rng.randint(1, 10)
                    )
                    rts = rt.strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute(
                        "INSERT INTO refund_requests VALUES (?, ?, ?, 'approved', ?)",
                        (request_id, order_id, rts, reason),
                    )
                    request_id += 1
                    conn.execute(
                        "INSERT INTO refunds VALUES (?, ?, ?, ?, ?)",
                        (refund_id, order_id, pay_amount, rts, reason),
                    )
                    refund_id += 1
                elif status == "completed" and rng.random() < 0.04:
                    # 被驳回的退款申请：只出现在 refund_requests，没有真实退款
                    rt = datetime.strptime(pay_time or "", "%Y-%m-%d %H:%M:%S") + timedelta(
                        days=rng.randint(1, 10)
                    )
                    conn.execute(
                        "INSERT INTO refund_requests VALUES (?, ?, ?, 'rejected', ?)",
                        (request_id, order_id, rt.strftime("%Y-%m-%d %H:%M:%S"), reason),
                    )
                    request_id += 1

                order_id += 1

        # ---- 行为事件（用于活跃用户口径）----
        event_id = 1
        rows = []
        for uid, reg in real_users:
            base = rng.random() ** 2
            d = max(date(YEAR, 1, 1), date.fromisoformat(reg))
            while d <= date(YEAR, 12, 31):
                if rng.random() < base * 0.25:
                    for et in rng.sample(["login", "view", "add_cart"], rng.randint(1, 3)):
                        rows.append((event_id, uid, d.isoformat(), et))
                        event_id += 1
                d += timedelta(days=1)
        conn.executemany("INSERT INTO user_events VALUES (?, ?, ?, ?)", rows)

        conn.commit()
    finally:
        conn.close()

    if path.exists():
        path.unlink()
    tmp.rename(path)
    logger.info("演示库已生成: {}（订单 {} 条）", path, order_id - 1)


def ensure_demo_db(path: Path) -> None:
    """演示库不存在时生成。"""
    if not path.exists():
        build_demo_db(path)


if __name__ == "__main__":
    import sys

    target = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[2] / "data" / "ecommerce.db"
    )
    build_demo_db(target)
