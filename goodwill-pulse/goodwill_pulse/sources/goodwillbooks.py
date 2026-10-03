"""Goodwillbooks storefront (books via Cash Monkey), derived from the truth world.

Native shape: sql/sources/goodwillbooks.sql
  * all money is INTEGER CENTS
  * order_date / refund_date are TEXT 'MM/DD/YYYY HH:MM' in Eastern wall time, no zone
  * store only from store_code '03' (2-digit text)
  * shipping_cents = shipping + handling; fee_cents = marketplace fee + payment fee
  * status COMPLETE | REFUNDED (refunds >= items + shipping; refunds exclude tax) | PARTIAL_REFUND; refund_cents = all refunds, refund_date = latest
  * monthly_statements: truth monthly payouts verbatim, paid the following month
  * inventory: one row per SKU (latest Goodwillbooks listing of the item)

Run: .venv/bin/python -m goodwill_pulse.sources.goodwillbooks [--truth PATH] [--out PATH] [--seed N]
"""
from __future__ import annotations

import random
from pathlib import Path

from goodwill_pulse.sources._common import record_dirty
from goodwill_pulse.sources.shopgoodwill import ET, _cli, _pick, _run

CHANNEL = "goodwillbooks"


def us_et(col: str, with_time: bool = True) -> str:
    fmt = "%m/%d/%Y %H:%M" if with_time else "%m/%d/%Y"
    return f"strftime(timezone('{ET}', {col}), '{fmt}')"


def cents(x: str) -> str:
    return f"CAST(round(coalesce({x}, 0) * 100) AS BIGINT)"


def build(truth_path: Path, out_path: Path, seed: int = 7) -> dict[str, int]:
    return _run("goodwillbooks", truth_path, out_path, [
        _orders, _lines, _statements, _inventory, lambda con: _dirty_rows(con, seed)])


def _orders(con) -> None:
    con.execute(f"""
        CREATE TEMP TABLE gwb_o AS
        SELECT o.*, b.native_buyer_ref, b.state, r.refund_total, r.last_refund
        FROM truth.orders o
        LEFT JOIN truth.buyers b ON b.buyer_id = o.buyer_id
        LEFT JOIN (SELECT order_id, sum(amount) refund_total, max(refunded_at) last_refund
                   FROM truth.refunds GROUP BY 1) r ON r.order_id = o.order_id
        WHERE o.channel = '{CHANNEL}'""")
    con.execute(f"""
        INSERT INTO sales_orders
        SELECT marketplace_order_id, {us_et('paid_at')}, native_buyer_ref, state,
               {cents('subtotal')}, {cents('shipping_charged + handling')}, {cents('tax')},
               {cents('marketplace_fee + payment_fee')}, {cents('total')},
               CASE WHEN coalesce(refund_total, 0) = 0 THEN 'COMPLETE'
                    WHEN refund_total >= subtotal + shipping_charged + handling THEN 'REFUNDED' ELSE 'PARTIAL_REFUND' END,
               {cents('refund_total')}, {us_et('last_refund')}
        FROM gwb_o ORDER BY paid_at, marketplace_order_id""")


def _lines(con) -> None:
    con.execute("""
        INSERT INTO sales_order_lines
        SELECT o.marketplace_order_id, ol.line_no, i.isbn, ol.item_id, i.title, right(i.store_id, 2),
               ol.quantity, CAST(round(ol.sale_price * 100 / greatest(ol.quantity, 1)) AS BIGINT)
        FROM gwb_o o JOIN truth.order_lines ol ON ol.order_id = o.order_id
        JOIN truth.items i ON i.item_id = ol.item_id
        ORDER BY o.marketplace_order_id, ol.line_no""")


def _statements(con) -> None:
    n = con.execute(f"SELECT count(*) FROM truth.payouts WHERE channel = '{CHANNEL}'").fetchone()[0]
    if n:
        con.execute(f"""
            INSERT INTO monthly_statements
            SELECT strftime(period_start, '%Y-%m'), {cents('gross')}, {cents('fees')}, {cents('refunds')},
                   {cents('net')}, paid_on
            FROM truth.payouts WHERE channel = '{CHANNEL}' ORDER BY period_start""")
        return
    # fallback: Eastern calendar month, paid on the 15th of the next month
    con.execute(f"""
        INSERT INTO monthly_statements
        WITH a AS (
            SELECT CAST(timezone('{ET}', paid_at) AS DATE) d, subtotal + shipping_charged + handling g,
                   marketplace_fee + payment_fee f, 0 r FROM gwb_o
            UNION ALL
            SELECT CAST(timezone('{ET}', r.refunded_at) AS DATE), 0, 0, r.amount
            FROM truth.refunds r JOIN gwb_o o ON o.order_id = r.order_id),
        m AS (SELECT CAST(date_trunc('month', d) AS DATE) m, sum(g) g, sum(f) f, sum(r) r FROM a GROUP BY 1)
        SELECT strftime(m, '%Y-%m'), {cents('g')}, {cents('f')}, {cents('r')}, {cents('g - f - r')},
               CAST(m + INTERVAL 1 MONTH + INTERVAL 14 DAY AS DATE)
        FROM m ORDER BY m""")


def _inventory(con) -> None:
    con.execute(f"""
        INSERT INTO inventory
        SELECT l.item_id, i.isbn, i.title, right(i.store_id, 2), {us_et('l.listed_at', False)},
               {us_et('l.ended_at', False)},
               CASE l.status WHEN 'active' THEN 'LISTED' WHEN 'sold' THEN 'SOLD' ELSE 'DELISTED' END,
               {cents('l.price')}
        FROM truth.listings l JOIN truth.items i ON i.item_id = l.item_id
        WHERE l.channel = '{CHANNEL}'
        QUALIFY row_number() OVER (PARTITION BY l.item_id ORDER BY l.listed_at DESC, l.listing_id DESC) = 1
        ORDER BY l.item_id""")


def _dirty_rows(con, seed: int) -> None:
    rng = random.Random(f"goodwillbooks-{seed}")
    lines = "SELECT order_no, line, sku, store_code FROM sales_order_lines ORDER BY order_no, line"
    # 1. missing store code (amounts unaffected; store recoverable from the SKU)
    row = _pick(con, lines, rng)
    if row:
        con.execute("UPDATE sales_order_lines SET store_code = NULL WHERE order_no = ? AND line = ?", row[:2])
        record_dirty(con, "sales_order_lines", f"{row[0]}/{row[1]}", "missing_store_code",
                     f"store_code NULL; SKU {row[2]} says '{row[3]}'")
    # 2. lower-case SKU prefix on another line
    row = _pick(con, f"SELECT * FROM ({lines}) WHERE store_code IS NOT NULL", rng)
    if row:
        con.execute("UPDATE sales_order_lines SET sku = lower(sku) WHERE order_no = ? AND line = ?", row[:2])
        record_dirty(con, "sales_order_lines", f"{row[0]}/{row[1]}", "lowercase_sku",
                     f"sku '{row[2].lower()}' should be '{row[2]}'")
    # 3. duplicate API row: a line repeated under the next line number
    row = _pick(con, "SELECT * FROM sales_order_lines ORDER BY order_no, line", rng)
    if row:
        nxt = con.execute("SELECT max(line) + 1 FROM sales_order_lines WHERE order_no = ?", [row[0]]).fetchone()[0]
        con.execute("INSERT INTO sales_order_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [row[0], nxt, *row[2:]])
        record_dirty(con, "sales_order_lines", f"{row[0]}/{nxt}", "duplicate_row",
                     f"duplicate of line {row[1]} (sku {row[3]}); count it once")
    # 4. test order (customer_ref 'TEST')
    last = con.execute("SELECT max(strptime(order_date, '%m/%d/%Y %H:%M')) FROM sales_orders").fetchone()[0]
    if last is not None:
        stamp = last.strftime("%m/%d/%Y %H:%M")
        con.execute("INSERT INTO sales_orders VALUES ('GWB999999', ?, 'TEST', 'IN', 100, 0, 0, 0, 100, 'COMPLETE', 0, NULL)",
                    [stamp])
        con.execute("INSERT INTO sales_order_lines VALUES ('GWB999999', 1, '9780000000000', 'GWM-03-999999', "
                    "'TEST ORDER - DO NOT SHIP', '03', 1, 100)")
        record_dirty(con, "sales_orders", "GWB999999", "test_order", "customer_ref 'TEST' staff test order; exclude")
        record_dirty(con, "sales_order_lines", "GWB999999/1", "test_order", "line of test order GWB999999; exclude")


if __name__ == "__main__":
    _cli("goodwillbooks", build)
