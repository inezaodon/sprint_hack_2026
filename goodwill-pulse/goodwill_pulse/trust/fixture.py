"""A tiny hand-built dataset in the data-contract shape, with answers worked out by hand.

The trust engine is tested against this until Odin's real `sales_line` / `books` tables exist; then run the same
runner with `--db`. DDL below is the data contract as the trust engine reads it.

sales_line (data through Sat 2026-10-03, so as_of = Sun 2026-10-04 and "yesterday" = 10-03):
  order  date   channel       department        store    items subtotal ship  buyer status
  O1     10-03  ShopGoodwill  Jewelry           Store07  1     40.00   8.00  B1    paid
  O2     10-03  ShopGoodwill  Jewelry           Store07  2     60.00  10.00  B2    paid
  O2     10-03  ShopGoodwill  Jewelry           Store07  0    -20.00   0.00  B2    refund   (partial refund)
  O3     10-03  ShopGoodwill  Textiles          Store03  1     15.00   5.00  B1    paid
  O4     10-03  eBay          Jewelry           Store03  1    120.00   0.00  B9    paid
  O5     10-03  eBay          Wares/Hard Goods  Store07  2     30.00  12.00  B1    paid     (2 lines)
  O5     10-03  eBay          Wares/Hard Goods  Store07  1     15.00   0.00  B1    paid
  O6     10-03  Amazon        Wares/Hard Goods  Store03  1     20.00   4.00  B5    unpaid   (must not count)
  O7     10-02  ShopGoodwill  Textiles          Store07  2     30.00   6.00  B3    paid
  O8     10-02  Amazon        Wares/Hard Goods  Store03  1     25.00   5.00  B4    paid

  10-03: sales 40+60-20+15+120+30+15 = 260.00 (gross 280.00, refunds 20.00) · orders O1..O5 = 5 · average 52.00 · shipping 35.00 · items 8
         customers (platform, buyer) = SGW:B1, SGW:B2, eBay:B9, eBay:B1 = 4
         by platform: ShopGoodwill 95.00 · eBay 165.00 · Amazon 0.00 (only the unpaid order)
         shipping by platform: ShopGoodwill 23.00 · eBay 12.00 · Amazon 0.00
         items by department: Jewelry 4 · Textiles 1 · Wares/Hard Goods 3 (Amazon's unpaid item excluded)
         jewelry pieces on ShopGoodwill = 1 + 2 = 3
  this week (Mon 09-28 .. 10-04): sales 260 + 55 = 315.00, orders 7, average 45.00

books (weekly, Monday dates): Store11, Store12, Store15 scan zero books for the last 3 weeks, so their sales fade.
  books sold per week: 09-07 251 · 09-14 213 · 09-21 170 · 09-28 156
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import duckdb

DDL = """
CREATE TABLE sales_line (
    order_id VARCHAR, paid_date DATE, channel VARCHAR, department VARCHAR, sku VARCHAR, origin_store VARCHAR,
    items INTEGER, subtotal DECIMAL(12,2), shipping_charged DECIMAL(12,2), buyer_id VARCHAR, status VARCHAR,
    source_file VARCHAR
);
CREATE TABLE books (
    week DATE, store VARCHAR, books_scanned INTEGER, books_sold INTEGER, books_revenue DECIMAL(12,2),
    source_file VARCHAR
);
"""

_F03, _F02 = "paid_orders_10-03-2026_10-03-2026.csv", "paid_orders_10-02-2026_10-02-2026.csv"
SALES = [
    ("O1", "2026-10-03", "ShopGoodwill", "Jewelry", "UP-07-000001", "Store07", 1, "40.00", "8.00", "B1", "paid", _F03),
    ("O2", "2026-10-03", "ShopGoodwill", "Jewelry", "UP-07-000002", "Store07", 2, "60.00", "10.00", "B2", "paid", _F03),
    ("O2", "2026-10-03", "ShopGoodwill", "Jewelry", "UP-07-000002", "Store07", 0, "-20.00", "0.00", "B2", "refund", _F03),
    ("O3", "2026-10-03", "ShopGoodwill", "Textiles", "UP-03-000003", "Store03", 1, "15.00", "5.00", "B1", "paid", _F03),
    ("O4", "2026-10-03", "eBay", "Jewelry", "UP-03-000004", "Store03", 1, "120.00", "0.00", "B9", "paid", _F03),
    ("O5", "2026-10-03", "eBay", "Wares/Hard Goods", "UP-07-000005", "Store07", 2, "30.00", "12.00", "B1", "paid", _F03),
    ("O5", "2026-10-03", "eBay", "Wares/Hard Goods", "UP-07-000006", "Store07", 1, "15.00", "0.00", "B1", "paid", _F03),
    ("O6", "2026-10-03", "Amazon", "Wares/Hard Goods", "UP-03-000007", "Store03", 1, "20.00", "4.00", "B5", "unpaid", _F03),
    ("O7", "2026-10-02", "ShopGoodwill", "Textiles", "UP-07-000008", "Store07", 2, "30.00", "6.00", "B3", "paid", _F02),
    ("O8", "2026-10-02", "Amazon", "Wares/Hard Goods", "UP-03-000009", "Store03", 1, "25.00", "5.00", "B4", "paid", _F02),
]

_WEEKS = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]
_BOOKS = {   # store: (scanned per week, sold per week)
    "Store03": ((120, 115, 118, 122), (80, 78, 81, 84)),
    "Store07": ((95, 98, 92, 97), (60, 62, 59, 63)),
    "Store11": ((70, 0, 0, 0), (45, 30, 12, 4)),
    "Store12": ((55, 0, 0, 0), (38, 25, 10, 3)),
    "Store15": ((40, 0, 0, 0), (28, 18, 8, 2)),
}
BOOK_PRICE = Decimal("6.25")
BOOKS = [(w, store, scanned[i], sold[i], sold[i] * BOOK_PRICE, f"cashmonkey_books_{w.isoformat()}.csv")
         for store, (scanned, sold) in _BOOKS.items() for i, w in enumerate(_WEEKS)]

AS_OF = date(2026, 10, 4)

# Planted answers: the same shape Stardess's demo-question file should use (see docs/TRUST_ENGINE.md).
ANSWERS = [
    {"question": "Why are book sales down? (stores scanning zero)", "metric": "books_scanned",
     "breakdown": "store", "period": "last_3_weeks", "expect": {"Store11": 0, "Store12": 0, "Store15": 0}},
    {"question": "Why are book sales down? (books sold by week)", "metric": "books_sold",
     "breakdown": "week", "period": "last_4_weeks",
     "expect": {"2026-09-07": 251, "2026-09-14": 213, "2026-09-21": 170, "2026-09-28": 156}},
    {"question": "How many jewelry pieces sold on ShopGoodwill yesterday?", "metric": "items_sold",
     "filters": {"channel": "ShopGoodwill", "department": "Jewelry"}, "period": "yesterday", "expect": 3},
    {"question": "How much did we take in on shipping yesterday, by platform?", "metric": "shipping_charged",
     "breakdown": "channel", "period": "yesterday", "expect": {"ShopGoodwill": 23.00, "eBay": 12.00, "Amazon": 0}},
    {"question": "What was our average sale this week?", "metric": "average_sale",
     "period": "this_week", "expect": 45.00},
    {"question": "Total sales yesterday (refund netted, unpaid excluded)", "metric": "total_sales",
     "period": "yesterday", "expect": 260.00},
    {"question": "Item sales before refunds yesterday", "metric": "gross_sales", "period": "yesterday",
     "expect": 280.00},
    {"question": "Refunds yesterday", "metric": "refunds", "period": "yesterday", "expect": 20.00},
    {"question": "Sales by platform yesterday", "metric": "total_sales", "breakdown": "channel",
     "period": "yesterday", "expect": {"ShopGoodwill": 95.00, "eBay": 165.00}},
    {"question": "Items sold by department yesterday", "metric": "items_sold", "breakdown": "department",
     "period": "yesterday", "expect": {"Jewelry": 4, "Textiles": 1, "Wares/Hard Goods": 3}},
    {"question": "Orders yesterday","metric": "orders", "period": "yesterday", "expect": 5},
    {"question": "Customers yesterday", "metric": "customers", "period": "yesterday", "expect": 4},
]


def connect() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB holding the fixture tables."""
    con = duckdb.connect()
    con.execute(DDL)
    con.executemany("INSERT INTO sales_line VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", SALES)
    con.executemany("INSERT INTO books VALUES (?, ?, ?, ?, ?, ?)", BOOKS)
    return con
