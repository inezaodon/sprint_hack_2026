"""GoodwillFinds.com (Shopify-style marketplace), derived from the truth world.

Native shape: sql/sources/goodwillfinds.sql
  * created_at / published_at are ISO TEXT in Eastern time with the DST-correct offset ('...T19:41:07-04:00')
  * store only from line_items.vendor 'Goodwill Michiana #03'
  * product_type: canonical category with messy casing ('jewelry', 'Jewelry', 'JEWELRY'), seeded per row
  * total_shipping_price = shipping + handling; marketplace_fee = marketplace fee + payment fee (Shopify bundles them)
  * financial_status: paid | refunded (refunds >= total) | partially_refunded
  * payouts: truth weekly payouts verbatim (amount = net, charges_gross = gross)

Run: .venv/bin/python -m goodwill_pulse.sources.goodwillfinds [--truth PATH] [--out PATH] [--seed N]
"""
from __future__ import annotations

import random
from pathlib import Path

from goodwill_pulse.sources._common import record_dirty
from goodwill_pulse.sources.shopgoodwill import ET, _cli, _pick, _run

CHANNEL = "goodwillfinds"
VENDOR = "'Goodwill Michiana #' || right({store}, 2)"


def iso_et(col: str) -> str:
    """SQL: TIMESTAMPTZ -> '2026-09-30T19:41:07-04:00' (Eastern wall time + DST-correct offset)."""
    return (f"(strftime(timezone('{ET}', {col}), '%Y-%m-%dT%H:%M:%S') || "
            f"CASE date_diff('minute', timezone('UTC', {col}), timezone('{ET}', {col})) "
            f"WHEN -240 THEN '-04:00' ELSE '-05:00' END)")


def messy(cat: str, key: str, seed: int) -> str:
    """SQL: seeded casing variant of a category (60% as-is, 30% lower, 10% upper)."""
    return (f"CASE hash({key} || '|{seed}') % 10 WHEN 0 THEN lower({cat}) WHEN 1 THEN lower({cat}) "
            f"WHEN 2 THEN lower({cat}) WHEN 3 THEN upper({cat}) ELSE {cat} END")


def build(truth_path: Path, out_path: Path, seed: int = 7) -> dict[str, int]:
    return _run("goodwillfinds", truth_path, out_path, [
        _orders, lambda con: _line_items(con, seed), _refunds, _payouts,
        lambda con: _products(con, seed), lambda con: _dirty_rows(con, seed)])


def _orders(con) -> None:
    con.execute(f"""
        CREATE TEMP TABLE gf_o AS
        WITH o AS (
            SELECT o.*, b.native_buyer_ref,  b.state,
                   TRY_CAST(nullif(regexp_extract(o.marketplace_order_id, '^\\s*(\\d+)', 1), '') AS BIGINT) AS num_id,
                   nullif(regexp_extract(o.marketplace_order_id, '(#GF\\d+)', 1), '') AS gf_name,
                   row_number() OVER (ORDER BY o.paid_at, o.order_id) AS seq,
                   r.refund_total
            FROM truth.orders o
            LEFT JOIN truth.buyers b ON b.buyer_id = o.buyer_id
            LEFT JOIN (SELECT order_id, sum(amount) refund_total FROM truth.refunds GROUP BY 1) r ON r.order_id = o.order_id
            WHERE o.channel = '{CHANNEL}')
        SELECT *, coalesce(num_id, 5400000000 + seq) AS gf_id, coalesce(gf_name, '#GF' || (10000 + seq)) AS gf_order_name
        FROM o""")
    con.execute(f"""
        INSERT INTO orders
        SELECT gf_id, gf_order_name, {iso_et('paid_at')},
               CASE WHEN coalesce(refund_total, 0) = 0 THEN 'paid'
                    WHEN refund_total >= total THEN 'refunded' ELSE 'partially_refunded' END,
               coalesce(TRY_CAST(nullif(regexp_extract(native_buyer_ref, '(\\d+)', 1), '') AS BIGINT),
                        CAST(hash(coalesce(native_buyer_ref, buyer_id)) % 1000000000000 AS BIGINT)),
               left(md5(coalesce(native_buyer_ref, buyer_id)), 16), state,
               subtotal, shipping_charged + handling, tax, total, marketplace_fee + payment_fee, 'USD'
        FROM gf_o ORDER BY gf_id""")


def _line_items(con, seed: int) -> None:
    con.execute(f"""
        INSERT INTO line_items
        SELECT 7100000000 + row_number() OVER (ORDER BY o.gf_id, ol.line_no), o.gf_id, ol.item_id, i.title,
               {VENDOR.format(store='i.store_id')}, {messy('i.category', 'ol.item_id', seed)},
               ol.sale_price, ol.quantity
        FROM gf_o o JOIN truth.order_lines ol ON ol.order_id = o.order_id
        JOIN truth.items i ON i.item_id = ol.item_id
        ORDER BY o.gf_id, ol.line_no""")


def _refunds(con) -> None:
    con.execute(f"""
        INSERT INTO refunds
        SELECT 8100000000 + row_number() OVER (ORDER BY r.refunded_at, r.refund_id), o.gf_id,
               {iso_et('r.refunded_at')}, r.amount, r.reason
        FROM truth.refunds r JOIN gf_o o ON o.order_id = r.order_id
        ORDER BY r.refunded_at, r.refund_id""")


def _payouts(con) -> None:
    n = con.execute(f"SELECT count(*) FROM truth.payouts WHERE channel = '{CHANNEL}'").fetchone()[0]
    if n:
        con.execute(f"""
            INSERT INTO payouts
            SELECT 91000000 + row_number() OVER (ORDER BY period_start, payout_id), paid_on, net, 'paid',
                   gross, fees, refunds
            FROM truth.payouts WHERE channel = '{CHANNEL}' ORDER BY period_start, payout_id""")
        return
    # fallback: Mon-Sun weeks by Eastern date, paid the following Wednesday
    con.execute(f"""
        INSERT INTO payouts
        WITH a AS (
            SELECT CAST(timezone('{ET}', paid_at) AS DATE) d, subtotal + shipping_charged + handling g,
                   marketplace_fee + payment_fee f, 0 r FROM gf_o
            UNION ALL
            SELECT CAST(timezone('{ET}', r.refunded_at) AS DATE), 0, 0, r.amount
            FROM truth.refunds r JOIN gf_o o ON o.order_id = r.order_id),
        w AS (SELECT CAST(date_trunc('week', d) AS DATE) wk, sum(g) g, sum(f) f, sum(r) r FROM a GROUP BY 1)
        SELECT 91000000 + row_number() OVER (ORDER BY wk), wk + 9, g - f - r, 'paid', g, f, r FROM w ORDER BY wk""")


def _products(con, seed: int) -> None:
    con.execute(f"""
        INSERT INTO products
        SELECT coalesce(TRY_CAST(l.native_listing_id AS BIGINT), 6100000000 + row_number() OVER (ORDER BY l.listing_id)),
               l.item_id, i.title, {VENDOR.format(store='i.store_id')}, {messy('i.category', 'l.listing_id', seed)},
               {iso_et('l.listed_at')}, {iso_et('l.ended_at')},
               CASE l.status WHEN 'active' THEN 'active' WHEN 'sold' THEN 'sold' ELSE 'archived' END, l.price
        FROM truth.listings l JOIN truth.items i ON i.item_id = l.item_id
        WHERE l.channel = '{CHANNEL}' ORDER BY l.listing_id""")


def _dirty_rows(con, seed: int) -> None:
    rng = random.Random(f"goodwillfinds-{seed}")
    # 1. vendor typo (prefer a Store07 line, as seen in the wild); amounts unaffected
    row = (_pick(con, "SELECT id, vendor FROM line_items WHERE vendor LIKE '%#07' ORDER BY id", rng)
           or _pick(con, "SELECT id, vendor FROM line_items ORDER BY id", rng))
    if row:
        bad = row[1].replace("Goodwill", "Goodwil", 1)
        con.execute("UPDATE line_items SET vendor = ? WHERE id = ?", [bad, row[0]])
        record_dirty(con, "line_items", row[0], "vendor_typo", f"vendor '{bad}' should be '{row[1]}'")
    # 2. duplicate API row: a line item delivered twice under a new id
    row = _pick(con, "SELECT * FROM line_items ORDER BY id", rng)
    if row:
        new_id = con.execute("SELECT max(id) + 1 FROM line_items").fetchone()[0]
        con.execute("INSERT INTO line_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [new_id, *row[1:]])
        record_dirty(con, "line_items", new_id, "duplicate_row",
                     f"duplicate of line_items.id {row[0]} (order {row[1]}, sku {row[2]}); count it once")
    # 3. test order placed by staff (email_hash 'TEST', name '#GF-TEST')
    last = con.execute("SELECT max(created_at), max(id) FROM orders").fetchone()
    if last[0] is not None:
        oid = last[1] + 1
        con.execute("INSERT INTO orders VALUES (?, '#GF-TEST', ?, 'paid', 0, 'TEST', 'IN', 1.00, 0, 0, 1.00, 0, 'USD')",
                    [oid, last[0]])
        lid = con.execute("SELECT max(id) + 1 FROM line_items").fetchone()[0]
        con.execute("INSERT INTO line_items VALUES (?, ?, 'UP-03-999999', 'TEST ORDER - DO NOT SHIP', "
                    "'Goodwill Michiana #03', 'Collectibles', 1.00, 1)", [lid, oid])
        record_dirty(con, "orders", oid, "test_order", "staff test order '#GF-TEST' (email_hash 'TEST'); exclude")
        record_dirty(con, "line_items", lid, "test_order", f"line of test order {oid}; exclude")


if __name__ == "__main__":
    _cli("goodwillfinds", build)
