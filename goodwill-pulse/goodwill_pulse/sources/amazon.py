"""Amazon Seller Central source DB (books via Cash Monkey, merchant fulfilled) derived from the truth world.

    python -m goodwill_pulse.sources.amazon [--truth data/sources/_truth.duckdb] [--out data/sources/amazon.duckdb]

Native quirks reproduced (see sql/sources/amazon.sql):
  * orders.purchase_date / last_updated_date: ISO-8601 UTC text '2026-09-30T23:41:07Z'
  * financial_events.date_time: Pacific text with PST/PDT abbreviation 'Sep 30, 2026 4:41:07 PM PDT'
  * one 'Order' financial event per order item; fees are NEGATIVE; refunds are type='Refund' with negative
    product_sales / shipping_credits; one 'Transfer' row per settlement (total = -deposit) dated on the deposit date
  * settlements every 14 days (truth payouts), deposit_date = truth paid_on, total_amount = truth net
  * listings.open_date: Pacific text 'YYYY-MM-DD HH:MM:SS PDT'; ASIN = ISBN-10 for 978- books
  * store only knowable from seller_sku 'GWM-<store>-<n>'

Money rules (so the harmonizer can reconcile):
  sum(order_items.item_price) per order = truth subtotal; shipping_price = truth shipping_charged + handling,
  item_tax = truth tax (allocated by price); order_total_amount = truth total.
  Order event: product_sales = item_price, shipping_credits = shipping_price, selling_fees = -marketplace_fee share,
  other_transaction_fees = -payment_fee share. Refund event: -amount split product first, then shipping.
  Events are assigned to the settlement whose [start, end] contains the event's ET business date (as truth does).
  Sum of non-Transfer events in a settlement == settlement total_amount. If truth has a residual the events do not
  explain, a balancing 'Service Fee' event is added and counted in build stats (`_residual_events`).
"""
from __future__ import annotations

import argparse
import random
import time
from collections import defaultdict
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from pathlib import Path

from ._common import (
    EASTERN, PACIFIC, SOURCES_DIR, SQL_SOURCES_DIR, TRUTH_PATH, IdMaker, PeriodIndex, allocate, atomic_duckdb,
    create_from_ddl, insert_rows, iso_utc, load_truth_channel, local_date, money, open_truth, pacific_amazon,
    pacific_text, record_dirty, table_counts, to_utc, UTC,
)

DDL = SQL_SOURCES_DIR / "amazon.sql"
OUT_PATH = SOURCES_DIR / "amazon.duckdb"
PAYOUT_TZ = EASTERN  # truth: payout periods are by business date (America/New_York)
ZERO = Decimal("0.00")


def isbn13_to_10(isbn: str | None) -> str | None:
    if not isbn:
        return None
    d = "".join(ch for ch in isbn if ch.isdigit())
    if len(d) == 10:
        return d
    if len(d) != 13 or not d.startswith("978"):
        return None
    core = d[3:12]
    s = sum((10 - i) * int(c) for i, c in enumerate(core))
    check = (11 - s % 11) % 11
    return core + ("X" if check == 10 else str(check))


def _asin(item_id: str, isbn: str | None, cache: dict, rng: random.Random) -> str:
    if item_id not in cache:
        a = isbn13_to_10(isbn)
        if a is None:
            a = "B0" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(8))
        cache[item_id] = a
    return cache[item_id]


def build(truth_path: Path = TRUTH_PATH, out_path: Path = OUT_PATH, seed: int = 7) -> dict[str, int]:
    rng = random.Random(f"amazon:{seed}")
    ids = IdMaker(rng)
    tcon = open_truth(truth_path)
    try:
        t = load_truth_channel(tcon, "amazon")
    finally:
        tcon.close()
    now: datetime = t["now"]
    lines_by_order: dict[str, list[dict]] = defaultdict(list)
    for ln in t["lines"]:
        lines_by_order[ln["order_id"]].append(ln)
    refunds_by_order: dict[str, list[dict]] = defaultdict(list)
    for r in t["refunds"]:
        refunds_by_order[r["order_id"]].append(r)

    # ---- settlements
    settlements, sid_of = [], {}
    for p in t["payouts"]:
        sid = ids.digits(11, "")
        sid_of[p["payout_id"]] = sid
        settlements.append({"settlement_id": sid, "settlement_start_date": p["period_start"],
                            "settlement_end_date": p["period_end"], "deposit_date": p["paid_on"],
                            "total_amount": money(p["net"])})
    pidx = PeriodIndex((s["settlement_start_date"], s["settlement_end_date"], s["settlement_id"]) for s in settlements)

    def settlement_for(ts: datetime) -> str | None:
        return pidx.find(local_date(ts, PAYOUT_TZ))

    asins: dict[str, str] = {}
    orders, items, events = [], [], []  # events: (utc_ts, sort_key, row)

    for o in t["orders"]:
        lines = lines_by_order.get(o["order_id"], [])
        paid = o["paid_at"]
        refs = refunds_by_order.get(o["order_id"], [])
        shipped = paid < now - timedelta(days=1)
        ship_at = paid + timedelta(minutes=rng.randint(4 * 60, 30 * 60))
        last = max([min(ship_at, now) if shipped else paid] + [r["refunded_at"] for r in refs])
        orders.append({
            "amazon_order_id": o["marketplace_order_id"], "purchase_date": iso_utc(paid),
            "last_updated_date": iso_utc(last), "order_status": "Shipped" if shipped else "Unshipped",
            "fulfillment_channel": "MFN", "sales_channel": "Amazon.com", "buyer_email": o["native_buyer_ref"],
            "ship_state": o["buyer_state"], "order_total_amount": money(o["total"]), "order_total_currency": "USD",
        })
        prices = [money(ln["sale_price"]) * int(ln["quantity"] or 1) for ln in lines]
        ship = allocate(money(o["shipping_charged"]) + money(o["handling"]), prices)
        tax = allocate(o["tax"], prices)
        fee_w = [ln["fee_alloc"] if ln["fee_alloc"] is not None else p for ln, p in zip(lines, prices)]
        sell_fee = allocate(o["marketplace_fee"], fee_w)
        pay_fee = allocate(o["payment_fee"], fee_w)
        sid = settlement_for(paid)
        for k, ln in enumerate(lines):
            oiid = ids.digits(14, "")
            items.append({
                "amazon_order_id": o["marketplace_order_id"], "order_item_id": oiid, "seller_sku": ln["item_id"],
                "asin": _asin(ln["item_id"], ln["isbn"], asins, rng), "title": ln["title"],
                "quantity_ordered": int(ln["quantity"] or 1), "item_price": prices[k], "shipping_price": ship[k],
                "item_tax": tax[k],
            })
            total = prices[k] + ship[k] - sell_fee[k] - pay_fee[k]
            events.append((paid, (o["marketplace_order_id"], k), {
                "date_time": pacific_amazon(paid), "settlement_id": sid, "type": "Order",
                "order_id": o["marketplace_order_id"], "sku": ln["item_id"], "description": ln["title"],
                "quantity": int(ln["quantity"] or 1), "marketplace": "amazon.com", "fulfillment": "Seller",
                "product_sales": prices[k], "shipping_credits": ship[k], "promotional_rebates": ZERO,
                "selling_fees": -sell_fee[k], "fba_fees": ZERO, "other_transaction_fees": -pay_fee[k],
                "other": ZERO, "total": total,
            }))
        sub = money(o["subtotal"])
        main = max(range(len(lines)), key=lambda i: prices[i]) if lines else None
        for r in refs:
            amt = money(r["amount"])
            prod = min(amt, sub)
            events.append((r["refunded_at"], (o["marketplace_order_id"], 1000 + len(events)), {
                "date_time": pacific_amazon(r["refunded_at"]), "settlement_id": settlement_for(r["refunded_at"]),
                "type": "Refund", "order_id": o["marketplace_order_id"],
                "sku": lines[main]["item_id"] if main is not None else None,
                "description": lines[main]["title"] if main is not None else None, "quantity": 1,
                "marketplace": "amazon.com", "fulfillment": "Seller", "product_sales": -prod,
                "shipping_credits": -(amt - prod), "promotional_rebates": ZERO, "selling_fees": ZERO,
                "fba_fees": ZERO, "other_transaction_fees": ZERO, "other": ZERO, "total": -amt,
            }))

    # ---- balancing + transfer rows per settlement
    by_sid: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for _, _, ev in events:
        if ev["settlement_id"]:
            by_sid[ev["settlement_id"]] += ev["total"]
    residual_events = 0
    for s in settlements:
        sid, end = s["settlement_id"], s["settlement_end_date"]
        resid = s["total_amount"] - by_sid[sid]
        close_ts = datetime.combine(end, dtime(23, 30), PAYOUT_TZ).astimezone(UTC).replace(tzinfo=None)
        if resid != ZERO:
            residual_events += 1
            events.append((close_ts, ("~", sid), {
                "date_time": pacific_amazon(close_ts), "settlement_id": sid, "type": "Service Fee",
                "order_id": None, "sku": None,
                "description": "Subscription" if resid < 0 else "Adjustment - reserve release", "quantity": None,
                "marketplace": "amazon.com", "fulfillment": None, "product_sales": ZERO, "shipping_credits": ZERO,
                "promotional_rebates": ZERO, "selling_fees": ZERO, "fba_fees": ZERO,
                "other_transaction_fees": ZERO, "other": resid, "total": resid,
            }))
        dep_ts = datetime.combine(s["deposit_date"], dtime(9, 15), PACIFIC).astimezone(UTC).replace(tzinfo=None)
        events.append((dep_ts, ("~~", sid), {
            "date_time": pacific_amazon(dep_ts), "settlement_id": sid, "type": "Transfer", "order_id": None,
            "sku": None, "description": "To account ending in: 0101", "quantity": None, "marketplace": "amazon.com",
            "fulfillment": None, "product_sales": ZERO, "shipping_credits": ZERO, "promotional_rebates": ZERO,
            "selling_fees": ZERO, "fba_fees": ZERO, "other_transaction_fees": ZERO, "other": -s["total_amount"],
            "total": -s["total_amount"],
        }))
    events.sort(key=lambda e: (e[0], e[1]))
    fin = []
    for _, _, ev in events:
        fin.append({"event_id": ids.digits(12, ""), **ev})

    # ---- listings (one offer per seller_sku; Amazon re-opens the same sku on relist)
    by_item: dict[str, list[dict]] = defaultdict(list)
    for li in t["listings"]:
        by_item[li["item_id"]].append(li)
    listings = []
    for item_id, ls in sorted(by_item.items()):
        first, cur = ls[0], ls[-1]
        status = {"active": "Active", "sold": "Sold"}.get(cur["status"], "Inactive")
        listings.append({
            "seller_sku": item_id, "asin": _asin(item_id, cur["isbn"], asins, rng), "item_name": cur["title"],
            "open_date": pacific_text(first["listed_at"]), "price": money(cur["price"]),
            "quantity": 1 if status == "Active" else 0, "status": status,
        })

    # ---- deliberate dirty data (documented in _dirty_data)
    dirty: list[tuple] = []
    real_items = list(items)
    if real_items:
        a = rng.choice(real_items)
        orig = a["seller_sku"]
        a["seller_sku"] = orig[:3].lower() + orig[3:]
        dirty.append(("order_items", f"{a['amazon_order_id']}/{a['order_item_id']}", "lowercase_sku_prefix",
                      f"seller_sku {orig!r} exported as {a['seller_sku']!r}"))
        cand = [r for r in real_items if r is not a]
        if cand:
            b = rng.choice(cand)
            orig = b["seller_sku"]
            parts = orig.split("-")
            b["seller_sku"] = f"{parts[0]}--{parts[-1]}"
            dirty.append(("order_items", f"{b['amazon_order_id']}/{b['order_item_id']}", "missing_store_code",
                          f"seller_sku {orig!r} exported without store code as {b['seller_sku']!r}"))
    order_evs = [e for e in fin if e["type"] == "Order"]
    if order_evs:
        src = rng.choice(order_evs)
        dup = {**src, "event_id": ids.digits(12, "")}
        fin.append(dup)
        dirty.append(("financial_events", dup["event_id"], "duplicate_api_row",
                      f"same Order event as event_id {src['event_id']} (order {src['order_id']}) downloaded twice"))
    if orders:
        tmpl = orders[len(orders) // 2]
        test_id = f"113-{ids.digits(7)}-{ids.digits(7)}"
        orders.append({**tmpl, "amazon_order_id": test_id, "buyer_email": "TEST", "ship_state": "IN",
                       "order_total_amount": Decimal("1.00")})
        test_item = ids.digits(14)
        items.append({"amazon_order_id": test_id, "order_item_id": test_item, "seller_sku": "GWM-01-000000",
                      "asin": "0000000000", "title": "TEST ORDER - DO NOT SHIP", "quantity_ordered": 1,
                      "item_price": Decimal("1.00"), "shipping_price": ZERO, "item_tax": ZERO})
        dirty.append(("orders", test_id, "test_order", "Seller Central test order, buyer_email = 'TEST'"))
        dirty.append(("order_items", f"{test_id}/{test_item}", "test_order", "line of the TEST order"))

    with atomic_duckdb(out_path) as con:
        create_from_ddl(con, DDL)
        con.execute("CREATE TABLE IF NOT EXISTS _dirty_data (table_name VARCHAR NOT NULL, native_key VARCHAR NOT NULL,"
                    " kind VARCHAR NOT NULL, note VARCHAR)")
        insert_rows(con, "orders", orders)
        insert_rows(con, "order_items", items)
        insert_rows(con, "financial_events", fin)
        insert_rows(con, "settlements", settlements)
        insert_rows(con, "listings", listings)
        for d in dirty:
            record_dirty(con, *d)
        counts = table_counts(con)
    counts["_residual_events"] = residual_events
    return counts


# ---------------------------------------------------------------- reconciliation
def reconcile(truth_path: Path = TRUTH_PATH, out_path: Path = OUT_PATH) -> list[dict]:
    """Per ET month: truth vs native order count / subtotal (dirt excluded) and payout net (by deposit month)."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"ATTACH '{truth_path}' AS t (READ_ONLY)")
    con.execute(f"ATTACH '{out_path}' AS n (READ_ONLY)")
    con.execute("SET TimeZone = 'America/New_York'")
    rows = con.execute("""
    WITH tr AS (
        SELECT strftime(paid_at, '%Y-%m') AS month, count(*) AS orders, sum(subtotal) AS subtotal
        FROM t.orders WHERE channel = 'amazon' GROUP BY 1),
    nt AS (
        SELECT strftime(strptime(o.purchase_date, '%Y-%m-%dT%H:%M:%SZ')::TIMESTAMP AT TIME ZONE 'UTC', '%Y-%m') AS month,
               count(DISTINCT o.amazon_order_id) AS orders, sum(i.item_price) AS subtotal
        FROM n.orders o JOIN n.order_items i USING (amazon_order_id)
        WHERE o.amazon_order_id NOT IN (SELECT native_key FROM n._dirty_data WHERE kind = 'test_order')
        GROUP BY 1),
    tp AS (SELECT strftime(paid_on, '%Y-%m') AS month, sum(net) AS net FROM t.payouts WHERE channel = 'amazon' GROUP BY 1),
    np AS (SELECT strftime(deposit_date, '%Y-%m') AS month, sum(total_amount) AS net FROM n.settlements GROUP BY 1),
    months AS (SELECT month FROM tr UNION SELECT month FROM tp)
    SELECT m.month, tr.orders, nt.orders, tr.subtotal, nt.subtotal, tp.net, np.net
    FROM months m LEFT JOIN tr USING (month) LEFT JOIN nt USING (month) LEFT JOIN tp USING (month)
    LEFT JOIN np USING (month) ORDER BY 1""").fetchall()
    con.close()
    keys = ["month", "truth_orders", "native_orders", "truth_subtotal", "native_subtotal", "truth_payout_net",
            "native_payout_net"]
    return [dict(zip(keys, r)) for r in rows]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--truth", type=Path, default=TRUTH_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    counts = build(a.truth, a.out, a.seed)
    print(f"amazon: built {a.out} in {time.perf_counter() - t0:.1f}s")
    for k, v in counts.items():
        print(f"  {k:<20} {v:>8}")


if __name__ == "__main__":
    main()
