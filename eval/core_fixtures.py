"""Small synthetic finance, stock and support fixtures with independently stated oracles."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from app.analytics.semantics import BusinessProfile


def build_core_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE receipts(id INTEGER,day TEXT,amount INTEGER,region TEXT,status TEXT);
            INSERT INTO receipts VALUES
                (1,'2025-12-01',12345,'华东','confirmed'),
                (2,'2025-12-02',20000,'华南','confirmed'),
                (3,'2025-12-03',90000,'华东','draft'),
                (4,'2025-12-04',0,'零值区域','confirmed'),
                (5,'2025-11-01',100,'华东','confirmed');
            CREATE TABLE stock(id INTEGER,day TEXT,quantity INTEGER,price NUMERIC,warehouse TEXT);
            INSERT INTO stock VALUES (1,'2025-12-31',10,20,'一号库'),(2,'2025-12-31',5,10,'二号库');
            CREATE TABLE support(id INTEGER,day TEXT,resolved INTEGER,total INTEGER,team TEXT);
            INSERT INTO support VALUES (1,'2025-12-01',1,10,'A'),(2,'2025-12-02',90,100,'B');
            CREATE TABLE zero_support AS SELECT * FROM support WHERE 0;
            INSERT INTO zero_support VALUES(1,'2025-12-01',0,0,'A');
            CREATE TABLE duplicate_receipts AS SELECT * FROM receipts;
            INSERT INTO duplicate_receipts VALUES(1,'2025-12-01',12345,'华东','confirmed');
            CREATE TABLE missing_receipts AS SELECT * FROM receipts;
            UPDATE missing_receipts SET amount=NULL WHERE id=1;
            CREATE TABLE invalid_dates AS SELECT * FROM receipts;
            UPDATE invalid_dates SET day='2025-02-30' WHERE id=1;
        """)


def domain_profile(domain: str, table: str | None = None) -> BusinessProfile:
    if domain == "finance":
        metric = {
            "id": "income",
            "name": "实收收入",
            "table": table or "receipts",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "sum",
            "storage_unit": "分",
            "value_column": "amount",
            "dimensions": ["region"],
            "filter_columns": ["status"],
            "filters": [{"column": "status", "values": ["confirmed"]}],
        }
    elif domain == "stock":
        metric = {
            "id": "quantity",
            "name": "库存数量",
            "table": table or "stock",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "sum",
            "storage_unit": "件",
            "value_column": "quantity",
            "dimensions": ["warehouse"],
        }
    elif domain == "stock_average":
        metric = {
            "id": "average_price",
            "name": "平均单价",
            "table": "stock",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "average",
            "storage_unit": "元",
            "value_column": "price",
        }
    elif domain == "stock_count":
        metric = {
            "id": "records",
            "name": "库存记录",
            "table": "stock",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "count",
            "storage_unit": "条",
        }
    elif domain == "schema_drift":
        metric = {
            "id": "income",
            "name": "实收收入",
            "table": "receipts",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "sum",
            "storage_unit": "分",
            "value_column": "amount_missing",
        }
    else:
        metric = {
            "id": "resolution",
            "name": "解决率",
            "table": table or "support",
            "primary_key": ["id"],
            "date_column": "day",
            "aggregation": "ratio",
            "storage_unit": "比例",
            "numerator_column": "resolved",
            "denominator_column": "total",
            "dimensions": ["team"],
        }
    if domain in {"stock", "stock_average"}:
        metric["time_behavior"] = "snapshot"
    return BusinessProfile.model_validate(
        {
            "id": domain,
            "name": {
                "finance": "财务",
                "stock": "库存",
                "stock_average": "单价",
                "stock_count": "库存记录",
                "schema_drift": "结构变更",
            }.get(domain, "客服"),
            "metrics": [metric],
        }
    )
