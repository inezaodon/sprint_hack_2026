"""eBay source DB (Sell Fulfillment + Finances + Inventory APIs) derived from the truth world.

    python -m goodwill_pulse.sources.ebay [--truth data/sources/_truth.duckdb] [--out data/sources/ebay.duckdb]

ONE seller account sells both Upright general merchandise (sku 'UP-<store>-<n>') and Cash Monkey books
(sku 'GWM-<store>-<n>'); eBay itself has no notion of the listing tool.

Native quirks reproduced (see sql/sources/ebay.sql):
  * every money value is TEXT like "45.00"
  * every timestamp is ISO-8601 UTC TEXT with milliseconds '2026-09-30T23:41:07.000Z'
  * orders.pricingSummary_deliveryCost = truth shipping_charged + handling (Upright's $3/item handling is folded
    into the buyer's shipping on eBay); pricingSummary_total = truth total; tax is collected & remitted by eBay
  * totalMarketplaceFee = truth marketplace_fee + payment_fee (managed payments: one fee line)
  * transactions: SALE (transactionId = orderId, CREDIT, amount = subtotal + delivery - fees),
    REFUND (DEBIT, amount = refund), SHIPPING_LABEL (one per truth shipping_charges row with carrier 'ebay':
    DEBIT for a charge, CREDIT for a label refund; other carriers are billed outside eBay), NON_SALE_CHARGE (only to
    balance a truth payout residual, counted in `_residual_events`)
  * daily payouts (truth payouts) to bankLast4 '0101'; every transaction carries the payoutId of the payout whose
    period contains its ET business date (as truth does); pending ones have payoutId NULL
  * listings: a relist gets a NEW legacyItemId with relistParentId = previous legacyItemId
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
    EASTERN, SOURCES_DIR, SQL_SOURCES_DIR, TRUTH_PATH, UTC, IdMaker, PeriodIndex, allocate, atomic_duckdb, create_from_ddl,
    insert_rows, iso_utc, load_truth_channel, local_date, money, money_text, open_truth, record_dirty, table_counts,
)

DDL = SQL_SOURCES_DIR / "ebay.sql"
OUT_PATH = SOURCES_DIR / "ebay.duckdb"
PAYOUT_TZ = EASTERN  # truth: payout periods are by business date (America/New_York)
ZERO = Decimal("0.00")

# canonical category -> eBay US leaf-ish category id
EBAY_CATEGORY = {
    "Jewelry": "281", "Collectibles": "1", "Electronics": "293", "Clothing": "11450", "Shoes": "63889",
    "Home Decor": "10033", "Toys & Games": "220", "Art": "550", "Watches": "31387", "Books": "261186",
}


def _ms(rng: random.Random, ts: datetime) -> datetime:
    """eBay timestamps carry milliseconds; truth may be whole seconds, so add a deterministic sub-second part."""
    return ts if ts.microsecond else ts + timedelta(milliseconds=rng.randrange(1000))


def build(truth_path: Path = TRUTH_PATH, out_path: Path = OUT_PATH, seed: int = 7) -> dict[str, int]:
    rng = random.Random(f"ebay:{seed}")
    ids = IdMaker(rng)
    tcon = open_truth(truth_path)
    try:
        t = load_truth_channel(tcon, "ebay")
    finally:
        tcon.close()
    now: datetime = t["now"]
    lines_by_order: dict[str, list[dict]] = defaultdict(list)
    for ln in t["lines"]:
        lines_by_order[ln["order_id"]].append(ln)
    refunds_by_order: dict[str, list[dict]] = defaultdict(list)
    for r in t["refunds"]:
        refunds_by_order[r["order_id"]].append(r)

    payouts, pid_of = [], {}
    for p in t["payouts"]:
        pid = ids.digits(9, "6")
        pid_of[p["payout_id"]] = pid
        pay_ts = datetime.combine(p["paid_on"], dtime(rng.randint(9, 14), rng.randrange(60), rng.randrange(60)))
        payouts.append({"payoutId": pid, "payoutDate": iso_utc(_ms(rng, pay_ts), ms=True),
                        "amount": money(p["net"]), "payoutStatus": "SUCCEEDED", "bankLast4": "0101",
                        "_start": p["period_start"], "_end": p["period_end"]})
    pidx = PeriodIndex((p["_start"], p["_end"], p["payoutId"]) for p in payouts)
    payout_for = lambda ts: pidx.find(local_date(ts, PAYOUT_TZ))  # noqa: E731

    orders, line_items, txns = [], [], []  # txns: (ts, sortkey, row)
    mkt_of = {o["order_id"]: o["marketplace_order_id"] for o in t["orders"]}
    for o in t["orders"]:
        oid = o["marketplace_order_id"]
        lines = lines_by_order.get(o["order_id"], [])
        refs = refunds_by_order.get(o["order_id"], [])
        paid = _ms(rng, o["paid_at"])
        sub = money(o["subtotal"])
        delivery = money(o["shipping_charged"]) + money(o["handling"])
        fee = money(o["marketplace_fee"]) + money(o["payment_fee"])
        refunded = sum((money(r["amount"]) for r in refs), ZERO)
        total = money(o["total"])
        status = "PAID" if not refs else ("FULLY_REFUNDED" if refunded >= total else "PARTIALLY_REFUNDED")
        first_item = lines[0]["native_listing_id"] if lines else ids.digits(12, "1")
        orders.append({
            "orderId": oid, "legacyOrderId": f"{first_item}-{ids.digits(13, '2')}", "creationDate": iso_utc(paid, True),
            "orderPaymentStatus": status,
            "orderFulfillmentStatus": "FULFILLED" if o["paid_at"] < now - timedelta(days=1) else "NOT_STARTED",
            "buyer_username": o["native_buyer_ref"], "buyer_state": o["buyer_state"],
            "pricingSummary_priceSubtotal": money_text(sub), "pricingSummary_deliveryCost": money_text(delivery),
            "pricingSummary_tax": money_text(o["tax"]), "pricingSummary_total": money_text(total),
            "totalMarketplaceFee": money_text(fee), "currency": "USD",
        })
        prices = [money(ln["sale_price"]) * int(ln["quantity"] or 1) for ln in lines]
        ship = allocate(delivery, prices)
        for k, ln in enumerate(lines):
            line_items.append({
                "lineItemId": ids.digits(14, "1"), "orderId": oid, "legacyItemId": ln["native_listing_id"],
                "sku": ln["item_id"], "title": ln["title"], "categoryId": EBAY_CATEGORY.get(ln["category"], "99"),
                "quantity": int(ln["quantity"] or 1), "lineItemCost": money_text(prices[k]),
                "deliveryCost": money_text(ship[k]),
            })
        txns.append((paid, (oid, 0), {
            "transactionId": oid, "orderId": oid, "transactionType": "SALE", "transactionDate": iso_utc(paid, True),
            "amount": sub + delivery - fee, "totalFeeAmount": fee, "bookingEntry": "CREDIT",
            "payoutId": payout_for(paid)}))
        for j, r in enumerate(refs):
            rts = _ms(rng, r["refunded_at"])
            txns.append((rts, (oid, 2 + j), {
                "transactionId": ids.digits(10, "5"), "orderId": oid, "transactionType": "REFUND",
                "transactionDate": iso_utc(rts, True), "amount": money(r["amount"]), "totalFeeAmount": ZERO,
                "bookingEntry": "DEBIT", "payoutId": payout_for(r["refunded_at"])}))

    # ---- eBay-purchased labels: truth shipping_charges rows with carrier 'ebay' (deducted from eBay payouts)
    for ch in t["shipping_charges"]:
        if ch["carrier"] != "ebay":
            continue  # fedex / osm / pitney_bowes / easypost are billed outside eBay (bank 0101)
        amt = money(ch["amount"])
        cts = _ms(rng, ch["charged_at"])
        oid = mkt_of.get(ch["order_id"])
        txns.append((cts, (oid or "", ch["charge_id"]), {
            "transactionId": ids.digits(12, "3"), "orderId": oid, "transactionType": "SHIPPING_LABEL",
            "transactionDate": iso_utc(cts, True), "amount": abs(amt), "totalFeeAmount": ZERO,
            "bookingEntry": "DEBIT" if amt > 0 else "CREDIT", "payoutId": payout_for(ch["charged_at"])}))

    # ---- balance each payout against its transactions
    signed = defaultdict(lambda: ZERO)
    for _, _, x in txns:
        if x["payoutId"]:
            signed[x["payoutId"]] += x["amount"] if x["bookingEntry"] == "CREDIT" else -x["amount"]
    residual_events = 0
    for p in payouts:
        resid = p["amount"] - signed[p["payoutId"]]
        if resid != ZERO:
            residual_events += 1
            ts = datetime.combine(p["_end"], dtime(23, 0)) + timedelta(milliseconds=rng.randrange(1000))
            txns.append((ts, ("~", p["payoutId"]), {
                "transactionId": ids.digits(10, "7"), "orderId": None, "transactionType": "NON_SALE_CHARGE",
                "transactionDate": iso_utc(ts, True), "amount": abs(resid), "totalFeeAmount": ZERO,
                "bookingEntry": "CREDIT" if resid > 0 else "DEBIT", "payoutId": p["payoutId"]}))
    txns.sort(key=lambda x: (x[0], x[1]))
    transactions = [{**x, "amount": money_text(x["amount"]), "totalFeeAmount": money_text(x["totalFeeAmount"])}
                    for _, _, x in txns]

    listings = []
    for li in t["listings"]:
        listings.append({
            "legacyItemId": li["native_listing_id"], "sku": li["item_id"], "title": li["title"],
            "categoryId": EBAY_CATEGORY.get(li["category"], "99"),
            "listingStartDate": iso_utc(_ms(rng, li["listed_at"]), True),
            "listingEndDate": iso_utc(_ms(rng, li["ended_at"]), True) if li["ended_at"] else None,
            "price": money_text(li["price"]), "quantitySold": 1 if li["status"] == "sold" else 0,
            "listingStatus": {"active": "ACTIVE", "sold": "SOLD"}.get(li["status"], "ENDED"),
            "relistParentId": li["relist_of_native"],
        })

    # ---- deliberate dirty data
    dirty: list[tuple] = []
    if line_items:
        a = rng.choice(line_items)
        orig = a["sku"]
        a["sku"] = orig[:orig.index("-")].lower() + orig[orig.index("-"):]
        dirty.append(("line_items", a["lineItemId"], "lowercase_sku_prefix", f"sku {orig!r} returned as {a['sku']!r}"))
        cand = [r for r in line_items if r is not a]
        if cand:
            b = rng.choice(cand)
            orig = b["sku"]
            parts = orig.split("-")
            b["sku"] = f"{parts[0]}--{parts[-1]}"
            dirty.append(("line_items", b["lineItemId"], "missing_store_code",
                          f"sku {orig!r} returned without store code as {b['sku']!r}"))
    sales = [x for x in transactions if x["transactionType"] == "SALE"]
    if sales:
        src = rng.choice(sales)
        dup = {**src, "transactionId": src["transactionId"] + "-1"}
        transactions.append(dup)
        dirty.append(("transactions", dup["transactionId"], "duplicate_api_row",
                      f"SALE for order {src['orderId']} returned twice by getTransactions paging (orig {src['transactionId']})"))
    if orders:
        tmpl = orders[len(orders) // 2]
        test_id = f"{ids.digits(2)}-{ids.digits(5)}-{ids.digits(5)}"
        orders.append({**tmpl, "orderId": test_id, "legacyOrderId": None, "buyer_username": "TEST",
                       "pricingSummary_priceSubtotal": "1.00", "pricingSummary_deliveryCost": "0.00",
                       "pricingSummary_tax": "0.00", "pricingSummary_total": "1.00", "totalMarketplaceFee": "0.00",
                       "orderPaymentStatus": "PAID"})
        test_li = ids.digits(14, "1")
        line_items.append({"lineItemId": test_li, "orderId": test_id, "legacyItemId": None, "sku": "UP-01-000000",
                           "title": "TEST LISTING - DO NOT BUY", "categoryId": "99", "quantity": 1,
                           "lineItemCost": "1.00", "deliveryCost": "0.00"})
        dirty.append(("orders", test_id, "test_order", "sandbox test order leaked into production, buyer_username = 'TEST'"))
        dirty.append(("line_items", test_li, "test_order", "line of the TEST order"))

    for p in payouts:
        p["amount"] = money_text(p["amount"])
        del p["_start"], p["_end"]
    with atomic_duckdb(out_path) as con:
        create_from_ddl(con, DDL)
        con.execute("CREATE TABLE IF NOT EXISTS _dirty_data (table_name VARCHAR NOT NULL, native_key VARCHAR NOT NULL,"
                    " kind VARCHAR NOT NULL, note VARCHAR)")
        insert_rows(con, "orders", orders)
        insert_rows(con, "line_items", line_items)
        insert_rows(con, "transactions", transactions)
        insert_rows(con, "payouts", payouts)
        insert_rows(con, "listings", listings)
        for d in dirty:
            record_dirty(con, *d)
        counts = table_counts(con)
    counts["_residual_events"] = residual_events
    return counts


# ---------------------------------------------------------------- reconciliation
def reconcile(truth_path: Path = TRUTH_PATH, out_path: Path = OUT_PATH) -> list[dict]:
    """Per ET month and tool: truth vs native order count / subtotal (dirt excluded); payout net per month."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"ATTACH '{truth_path}' AS t (READ_ONLY)")
    con.execute(f"ATTACH '{out_path}' AS n (READ_ONLY)")
    con.execute("SET TimeZone = 'America/New_York'")
    rows = con.execute("""
    WITH tr AS (
        SELECT strftime(paid_at, '%Y-%m') AS month, tool, count(*) AS orders, sum(subtotal) AS subtotal
        FROM t.orders WHERE channel = 'ebay' GROUP BY ALL),
    nl AS (
        SELECT o.orderId,
               strftime(strptime(o.creationDate, '%Y-%m-%dT%H:%M:%S.%gZ')::TIMESTAMP AT TIME ZONE 'UTC', '%Y-%m') AS month,
               CASE WHEN upper(l.sku) LIKE 'GWM-%' THEN 'cashmonkey' ELSE 'upright' END AS tool,
               CAST(l.lineItemCost AS DECIMAL(12,2)) AS cost
        FROM n.orders o JOIN n.line_items l USING (orderId)
        WHERE o.orderId NOT IN (SELECT native_key FROM n._dirty_data WHERE kind = 'test_order')),
    nt AS (SELECT month, tool, count(DISTINCT orderId) AS orders, sum(cost) AS subtotal FROM nl GROUP BY ALL),
    tp AS (SELECT strftime(paid_on, '%Y-%m') AS month, sum(net) AS net FROM t.payouts WHERE channel = 'ebay' GROUP BY 1),
    np AS (SELECT strftime(strptime(payoutDate, '%Y-%m-%dT%H:%M:%S.%gZ'), '%Y-%m') AS month,
                  sum(CAST(amount AS DECIMAL(12,2))) AS net FROM n.payouts GROUP BY 1),
    keys AS (SELECT month, tool FROM tr UNION SELECT month, tool FROM nt)
    SELECT k.month, k.tool, tr.orders, nt.orders, tr.subtotal, nt.subtotal, tp.net, np.net
    FROM keys k LEFT JOIN tr USING (month, tool) LEFT JOIN nt USING (month, tool)
    LEFT JOIN tp USING (month) LEFT JOIN np USING (month) ORDER BY 1, 2""").fetchall()
    con.close()
    keys = ["month", "tool", "truth_orders", "native_orders", "truth_subtotal", "native_subtotal",
            "truth_payout_net", "native_payout_net"]
    return [dict(zip(keys, r)) for r in rows]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--truth", type=Path, default=TRUTH_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    counts = build(a.truth, a.out, a.seed)
    print(f"ebay: built {a.out} in {time.perf_counter() - t0:.1f}s")
    for k, v in counts.items():
        print(f"  {k:<20} {v:>8}")


if __name__ == "__main__":
    main()
