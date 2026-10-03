"""ShopGoodwill.com seller back office, derived from the truth world.

Native shape: sql/sources/shopgoodwill.sql
  * naive TIMESTAMPs in Pacific time (America/Los_Angeles)
  * sales: one row per item; ShippingCharged + Handling on the FIRST line of the order only; tax and refunds
    prorated across the order's lines by hammer price (remainder on the last line); refunds inline
    (an order with several refunds shows the summed amount and the latest RefundDate)
  * seller_fees: Commission per item (truth fee_alloc, or marketplace_fee prorated), PaymentProcessing per order
    on its first item
  * periodic_statements: Period 1 = days 1-10, 2 = 11-20, 3 = 21-EOM, amounts taken from truth payouts
    (Commission = commission booked in the period, PaymentFees = remaining fees). Periods follow the Eastern
    business date (as truth does), so a sale at 23:30 PT on the 10th lands in Period 2 although PaidDate shows the 10th

Run: .venv/bin/python -m goodwill_pulse.sources.shopgoodwill [--truth PATH] [--out PATH] [--seed N]

The private helpers below (_run, _prorated_lines, _pick, _cli) are reused by goodwillfinds.py and goodwillbooks.py;
atomic writes, DDL execution and dirty-data recording come from Engineer 2's _common.py.
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import duckdb
import numpy as np

from goodwill_pulse.sources._common import (
    DIRTY_DDL, SOURCES_DIR, SQL_SOURCES_DIR, TRUTH_PATH, atomic_duckdb, create_from_ddl, record_dirty, table_counts,
)

CHANNEL = "shopgoodwill"
PT = "America/Los_Angeles"
ET = "America/New_York"

# canonical category -> ShopGoodwill category name (harmonizer maps these back)
SGW_CATEGORY = {
    "Jewelry": "Jewelry & Gemstones",
    "Collectibles": "Collectibles",
    "Electronics": "Electronics",
    "Clothing": "Clothing",
    "Shoes": "Shoes",
    "Home Decor": "Home & Garden",
    "Toys & Games": "Toys & Hobbies",
    "Art": "Art",
    "Watches": "Watches",
    "Books": "Books",
}


# ---------------------------------------------------------------- private helpers (also used by GF / GWB modules)
def _run(name: str, truth_path: Path, out_path: Path, steps) -> dict[str, int]:
    """Build <out>.tmp atomically: native DDL + _dirty_data, truth ATTACHed read-only as `truth`, run steps."""
    truth_path = Path(truth_path)
    if not truth_path.exists():
        raise FileNotFoundError(f"truth DB not found: {truth_path} (run python -m goodwill_pulse.gen.truth)")
    with atomic_duckdb(out_path) as con:
        con.execute("SET TimeZone = 'UTC'")
        create_from_ddl(con, SQL_SOURCES_DIR / f"{name}.sql")
        con.execute(DIRTY_DDL)
        con.execute(f"ATTACH '{_q(truth_path)}' AS truth (READ_ONLY)")
        for step in steps:
            step(con)
        con.execute("DETACH truth")
        counts = table_counts(con)
    return counts


def _q(p) -> str:
    return str(p).replace("'", "''")


def _prorated_lines(con, src: str, amounts: dict[str, str], weight: str = "sale_price") -> None:
    """Add prorated columns <name> to temp table `src` (needs order_id, line_no, `weight` and order-level amounts).

    amounts = {new_column: order_level_amount_column}. 2 dp, rounding remainder lands on the order's last line.
    """
    for col, amt in amounts.items():
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE {src} AS
            WITH s AS (
                SELECT *, CAST(coalesce(round({amt} * {weight} / nullif(sum({weight}) OVER (PARTITION BY order_id), 0), 2), 0)
                              AS DECIMAL(12,2)) AS _share,
                       line_no = max(line_no) OVER (PARTITION BY order_id) AS _last
                FROM {src})
            SELECT * EXCLUDE (_share, _last),
                   CAST(CASE WHEN _last THEN coalesce({amt}, 0) - (sum(_share) OVER (PARTITION BY order_id) - _share)
                             ELSE _share END AS DECIMAL(12,2)) AS {col}
            FROM s""")


def _pick(con, sql: str, rng: random.Random):
    """Deterministically pick one row from an ordered query (None if empty)."""
    rows = con.execute(sql).fetchall()
    return rows[rng.randrange(len(rows))] if rows else None


def _cli(name: str, build) -> None:
    ap = argparse.ArgumentParser(prog=f"python -m goodwill_pulse.sources.{name}")
    ap.add_argument("--truth", type=Path, default=TRUTH_PATH)
    ap.add_argument("--out", type=Path, default=SOURCES_DIR / f"{name}.duckdb")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    t0 = time.perf_counter()
    counts = build(a.truth, a.out, a.seed)
    print(f"{a.out} built in {time.perf_counter() - t0:.1f}s")
    for t, n in counts.items():
        print(f"  {t:<22}{n:>10,}")


# ---------------------------------------------------------------- build
def build(truth_path: Path, out_path: Path, seed: int = 7) -> dict[str, int]:
    return _run("shopgoodwill", truth_path, out_path, [
        lambda con: _auctions(con, seed), _sales, _seller_fees, _statements, lambda con: _dirty_rows(con, seed)])


def _auctions(con, seed: int) -> None:
    df = con.execute(f"""
        SELECT l.listing_id, CAST(l.native_listing_id AS BIGINT) AS ItemID, l.item_id, i.title, i.category,
               timezone('{PT}', l.listed_at) AS StartTime, timezone('{PT}', l.ended_at) AS EndTime,
               l.status, CAST(l.price AS DOUBLE) AS price,
               CAST(r.native_listing_id AS BIGINT) AS RelistOfItemID,
               CAST(s.sale_price AS DOUBLE) AS hammer
        FROM truth.listings l
        JOIN truth.items i ON i.item_id = l.item_id
        LEFT JOIN truth.listings r ON r.listing_id = l.relist_of
        LEFT JOIN (SELECT ol.listing_id, max(ol.sale_price) AS sale_price
                   FROM truth.order_lines ol JOIN truth.orders o ON o.order_id = ol.order_id
                   WHERE o.channel = '{CHANNEL}' GROUP BY 1) s ON s.listing_id = l.listing_id
        WHERE l.channel = '{CHANNEL}'
        ORDER BY ItemID""").df()
    rng = np.random.default_rng(seed)
    n = len(df)
    u_start, u_bids, u_open = rng.uniform(0.4, 1.0, n), rng.uniform(0.5, 1.3, n), rng.integers(0, 6, n)
    starting, nbids, high = [], [], []
    for k, (status, price, hammer) in enumerate(zip(df.status, df.price, df.hammer)):
        if status == "sold" and hammer == hammer:            # sold with a known hammer price
            sb = max(0.99, min(hammer, round(min(price, hammer) * u_start[k]) - 0.01))
            steps = (hammer - sb) / max(1.0, sb * 0.1)
            nb = 1 if hammer <= sb else int(max(2, min(60, round(steps * u_bids[k]))))
            starting.append(sb); nbids.append(nb); high.append(hammer)
        elif status == "active":
            sb = max(0.99, round(price * u_start[k]) - 0.01)
            nb = int(u_open[k])
            starting.append(sb); nbids.append(nb); high.append(round(sb + nb * max(1.0, sb * 0.1), 2) if nb else None)
        else:                                                 # unsold: nobody met the opening bid
            starting.append(round(price, 2)); nbids.append(0); high.append(None)
    df["StartingBid"], df["NumBids"], df["HighBid"] = starting, nbids, high
    df["sgw_status"] = df.status.map({"active": "Open", "sold": "Sold", "unsold": "Unsold"}).fillna("Unsold")
    df["CategoryName"] = df.category.map(SGW_CATEGORY).fillna(df.category)
    con.register("auc_df", df)
    con.execute("""
        INSERT INTO auctions
        SELECT ItemID, item_id, title, CategoryName, StartTime, EndTime, CAST(StartingBid AS DECIMAL(12,2)),
               NumBids, CAST(HighBid AS DECIMAL(12,2)), sgw_status, RelistOfItemID
        FROM auc_df ORDER BY ItemID""")
    con.unregister("auc_df")


def _sales(con) -> None:
    con.execute(f"""
        CREATE TEMP TABLE sgw_lines AS
        SELECT o.order_id, CAST(o.marketplace_order_id AS BIGINT) AS OrderID, ol.line_no,
               CAST(li.native_listing_id AS BIGINT) AS ItemID, b.native_buyer_ref AS BuyerID, b.state AS BuyerState,
               timezone('{PT}', o.paid_at) AS PaidDate, ol.sale_price, ol.fee_alloc,
               o.shipping_charged, o.handling, o.tax, o.payment_type, o.marketplace_fee, o.payment_fee,
               sum(ol.fee_alloc) OVER (PARTITION BY o.order_id) AS fee_alloc_total,
               r.refund_total, timezone('{PT}', r.last_refund) AS RefundDate
        FROM truth.orders o
        JOIN truth.order_lines ol ON ol.order_id = o.order_id
        JOIN truth.listings li ON li.listing_id = ol.listing_id
        LEFT JOIN truth.buyers b ON b.buyer_id = o.buyer_id
        LEFT JOIN (SELECT order_id, sum(amount) AS refund_total, max(refunded_at) AS last_refund
                   FROM truth.refunds GROUP BY 1) r ON r.order_id = o.order_id
        WHERE o.channel = '{CHANNEL}'""")
    _prorated_lines(con, "sgw_lines", {"tax_line": "tax", "refund_line": "refund_total", "commission_line": "marketplace_fee"})
    con.execute("""
        INSERT INTO sales
        SELECT OrderID, ItemID, BuyerID, BuyerState, PaidDate, sale_price,
               CASE WHEN line_no = min(line_no) OVER (PARTITION BY order_id) THEN shipping_charged ELSE 0 END,
               CASE WHEN line_no = min(line_no) OVER (PARTITION BY order_id) THEN handling ELSE 0 END,
               tax_line, payment_type, refund_line > 0, refund_line,
               CASE WHEN refund_line > 0 THEN RefundDate END
        FROM sgw_lines ORDER BY OrderID, line_no""")


def _seller_fees(con) -> None:
    con.execute("""
        INSERT INTO seller_fees
        WITH f AS (
            SELECT OrderID, ItemID, line_no, 'Commission' AS FeeType,
                   CASE WHEN fee_alloc_total = marketplace_fee THEN fee_alloc ELSE commission_line END AS Amount,
                   PaidDate AS FeeDate
            FROM sgw_lines
            UNION ALL
            SELECT OrderID, ItemID, line_no, 'PaymentProcessing', payment_fee, PaidDate
            FROM sgw_lines QUALIFY line_no = min(line_no) OVER (PARTITION BY order_id))
        SELECT 100000000 + row_number() OVER (ORDER BY OrderID, line_no, FeeType), OrderID, ItemID, FeeType, Amount, FeeDate
        FROM f WHERE Amount > 0 ORDER BY OrderID, line_no, FeeType""")


def _et_date(col: str) -> str:
    """SQL: Pacific naive timestamp -> Eastern business date (truth payout periods use the ET business date)."""
    return f"CAST(timezone('{ET}', timezone('{PT}', {col})) AS DATE)"


def _period_activity_sql() -> str:
    """Native activity per Eastern business date (gross = hammer + shipping + handling)."""
    return f"""
        WITH d AS (
            SELECT {_et_date('PaidDate')} AS d, HammerPrice + ShippingCharged + Handling AS gross,
                   0 AS commission, 0 AS payment, 0 AS refunds FROM sales
            UNION ALL
            SELECT {_et_date('FeeDate')}, 0, CASE WHEN FeeType='Commission' THEN Amount ELSE 0 END,
                   CASE WHEN FeeType='PaymentProcessing' THEN Amount ELSE 0 END, 0 FROM seller_fees
            UNION ALL
            SELECT {_et_date('RefundDate')}, 0, 0, 0, RefundAmount FROM sales WHERE Refunded)
        SELECT d, sum(gross) gross, sum(commission) commission, sum(payment) payment, sum(refunds) refunds
        FROM d GROUP BY d"""


def _statements(con) -> None:
    con.execute(f"CREATE TEMP TABLE sgw_daily AS {_period_activity_sql()}")
    n = con.execute(f"SELECT count(*) FROM truth.payouts WHERE channel = '{CHANNEL}'").fetchone()[0]
    if n:   # truth payouts verbatim; Commission/PaymentFees split from booked commission in the period
        con.execute(f"""
            INSERT INTO periodic_statements
            SELECT 5000 + row_number() OVER (ORDER BY p.period_start),
                   year(p.period_start), month(p.period_start),
                   CASE WHEN day(p.period_start) <= 10 THEN 1 WHEN day(p.period_start) <= 20 THEN 2 ELSE 3 END,
                   p.period_start, p.period_end, p.gross,
                   least(p.fees, coalesce(c.commission, 0)), p.fees - least(p.fees, coalesce(c.commission, 0)),
                   p.refunds, p.net, p.paid_on
            FROM truth.payouts p
            LEFT JOIN LATERAL (SELECT sum(commission) AS commission FROM sgw_daily
                               WHERE d BETWEEN p.period_start AND p.period_end) c ON true
            WHERE p.channel = '{CHANNEL}' ORDER BY p.period_start""")
    else:   # no truth payouts: derive periods (ET business dates) from native activity, remit 2 days after end
        con.execute("""
            INSERT INTO periodic_statements
            WITH p AS (
                SELECT year(d) y, month(d) m, CASE WHEN day(d) <= 10 THEN 1 WHEN day(d) <= 20 THEN 2 ELSE 3 END AS per,
                       sum(gross) g, sum(commission) c, sum(payment) pf, sum(refunds) r
                FROM sgw_daily GROUP BY ALL),
            q AS (SELECT *, make_date(y, m, CASE per WHEN 1 THEN 1 WHEN 2 THEN 11 ELSE 21 END) AS ps FROM p)
            SELECT 5000 + row_number() OVER (ORDER BY ps), y, m, per, ps,
                   CASE per WHEN 3 THEN last_day(ps) ELSE ps + 9 END, g, c, pf, r, g - c - pf - r,
                   CASE per WHEN 3 THEN last_day(ps) ELSE ps + 9 END + 2
            FROM q ORDER BY ps""")


def _dirty_rows(con, seed: int) -> None:
    rng = random.Random(f"shopgoodwill-{seed}")
    # 1. duplicate API row: a Commission fee delivered twice under a new FeeID
    row = _pick(con, "SELECT * FROM seller_fees WHERE FeeType='Commission' ORDER BY FeeID", rng)
    if row:
        new_id = con.execute("SELECT max(FeeID) + 1 FROM seller_fees").fetchone()[0]
        con.execute("INSERT INTO seller_fees VALUES (?, ?, ?, ?, ?, ?)", [new_id, *row[1:]])
        record_dirty(con, "seller_fees", new_id, "duplicate_row",
               f"duplicate of FeeID {row[0]} (OrderID {row[1]}, ItemID {row[2]}); count it once")
    # 2. lower-case SKU prefix on a real auction (amounts unaffected)
    row = _pick(con, "SELECT ItemID, SellerItemCode FROM auctions WHERE Status='Sold' ORDER BY ItemID", rng)
    if row:
        con.execute("UPDATE auctions SET SellerItemCode = lower(SellerItemCode) WHERE ItemID = ?", [row[0]])
        record_dirty(con, "auctions", row[0], "lowercase_sku", f"SellerItemCode '{row[1].lower()}' should be '{row[1]}'")
    # 3. test order: a fake auction bought by BuyerID 'TEST' (exclude both rows)
    last = con.execute("SELECT (SELECT max(PaidDate) FROM sales), (SELECT max(ItemID) FROM auctions), "
                       "(SELECT max(OrderID) FROM sales)").fetchone()
    if last[0] is not None:
        item_id, order_id = (last[1] or 0) + 1, (last[2] or 0) + 1
        con.execute("INSERT INTO auctions VALUES (?, 'UP-03-999999', 'TEST LISTING - DO NOT BUY', 'Collectibles', "
                    "?, ?, 9.99, 1, 9.99, 'Sold', NULL)", [item_id, last[0], last[0]])
        con.execute("INSERT INTO sales VALUES (?, ?, 'TEST', 'IN', ?, 9.99, 0, 0, 0, 'CreditCard', false, 0, NULL)",
                    [order_id, item_id, last[0]])
        record_dirty(con, "auctions", item_id, "test_order", "test listing UP-03-999999; not a real item")
        record_dirty(con, "sales", f"{order_id}/{item_id}", "test_order", "BuyerID 'TEST' test purchase; exclude")


if __name__ == "__main__":
    _cli("shopgoodwill", build)
