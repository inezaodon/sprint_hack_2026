"""Engineer 2: Amazon + eBay native source DBs built from a tiny hand-made truth world.

The fixture writes the truth tables from CONTRACT section 1 (via gen.truth.TRUTH_DDL when it exists, otherwise a
minimal local DDL) with money that is internally consistent: payouts net = sales - fees - refunds (- eBay labels).
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from goodwill_pulse.sources import amazon, ebay
from goodwill_pulse.sources._common import allocate, iso_utc, money_text, pacific_amazon, pacific_text

UTC = timezone.utc
D = Decimal

try:  # prefer Engineer 1's DDL so the fixture tracks the real truth schema
    from goodwill_pulse.gen.truth import TRUTH_DDL  # type: ignore
except Exception:  # noqa: BLE001
    TRUTH_DDL = None

MIN_TRUTH_DDL = """
CREATE TABLE items (item_id VARCHAR PRIMARY KEY, store_id VARCHAR, line_of_business VARCHAR, category VARCHAR,
    title VARCHAR, isbn VARCHAR);
CREATE TABLE listings (listing_id VARCHAR PRIMARY KEY, item_id VARCHAR, channel VARCHAR, tool VARCHAR,
    native_listing_id VARCHAR, listed_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, status VARCHAR, price DECIMAL(12,2),
    relist_of VARCHAR);
CREATE TABLE buyers (buyer_id VARCHAR PRIMARY KEY, channel VARCHAR, native_buyer_ref VARCHAR, state VARCHAR);
CREATE TABLE orders (order_id VARCHAR PRIMARY KEY, channel VARCHAR, tool VARCHAR, marketplace_order_id VARCHAR,
    upright_order_id VARCHAR, buyer_id VARCHAR, paid_at TIMESTAMPTZ, item_count INTEGER, subtotal DECIMAL(12,2),
    shipping_charged DECIMAL(12,2), handling DECIMAL(12,2), tax DECIMAL(12,2), marketplace_fee DECIMAL(12,2),
    payment_fee DECIMAL(12,2), shipping_label_cost DECIMAL(12,2), total DECIMAL(12,2), payment_type VARCHAR);
CREATE TABLE order_lines (order_id VARCHAR, line_no INTEGER, item_id VARCHAR, listing_id VARCHAR, quantity INTEGER,
    sale_price DECIMAL(12,2), fee_alloc DECIMAL(12,2));
CREATE TABLE refunds (refund_id VARCHAR PRIMARY KEY, order_id VARCHAR, refunded_at TIMESTAMPTZ,
    amount DECIMAL(12,2), reason VARCHAR);
CREATE TABLE payouts (payout_id VARCHAR PRIMARY KEY, channel VARCHAR, period_start DATE, period_end DATE,
    paid_on DATE, gross DECIMAL(12,2), fees DECIMAL(12,2), refunds DECIMAL(12,2), net DECIMAL(12,2));
CREATE TABLE shipping_charges (charge_id VARCHAR PRIMARY KEY, carrier VARCHAR, order_id VARCHAR,
    charged_at TIMESTAMPTZ, amount DECIMAL(12,2), tracking VARCHAR);
"""

# Amazon order with a known Pacific rendering (PDT) and one in PST (after the Nov 1 2025 DST end)
KNOWN_PDT = datetime(2026, 9, 30, 23, 41, 7, tzinfo=UTC)      # 'Sep 30, 2026 4:41:07 PM PDT'
KNOWN_PST = datetime(2025, 11, 5, 17, 3, 44, tzinfo=UTC)      # 'Nov 5, 2025 9:03:44 AM PST'
MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
ISO_MS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _ins(con, table, row: dict):
    cols = ", ".join(row)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})",
                [str(v) if isinstance(v, Decimal) else v for v in row.values()])


def make_truth(path: Path, seed: int = 1) -> Path:
    rng = random.Random(seed)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone = 'UTC'")
    con.execute(TRUTH_DDL if TRUTH_DDL else MIN_TRUTH_DDL)
    days = [date(2025, 10, 27) + timedelta(days=i) for i in range(14)] + \
           [date(2026, 9, 1) + timedelta(days=i) for i in range(33)]
    n = {"item": 0, "order": 0, "buyer": 0, "listing": 0}
    ledger = []  # (channel, utc day, gross, fees, refunds, labels)

    def new_item(lob, store):
        n["item"] += 1
        pre = "UP" if lob == "general_merch" else "GWM"
        iid = f"{pre}-{store:02d}-{n['item']:06d}"
        cat = "Books" if lob == "books" else rng.choice(["Jewelry", "Clothing", "Electronics", "Art"])
        _ins(con, "items", {"item_id": iid, "store_id": f"Store{store:02d}", "line_of_business": lob, "category": cat,
                            "title": f"{cat} item {n['item']}", "isbn": f"978{rng.randrange(10**9, 10**10)}"
                            if lob == "books" else None})
        return iid

    def new_listing(iid, channel, tool, at, status, price, relist_of=None, ended=None):
        n["listing"] += 1
        lid = f"L{n['listing']:06d}"
        native = iid if channel == "amazon" else str(110000000000 + n["listing"])
        _ins(con, "listings", {"listing_id": lid, "item_id": iid, "channel": channel, "tool": tool,
                               "native_listing_id": native, "listed_at": at, "ended_at": ended, "status": status,
                               "price": price, "relist_of": relist_of})
        return lid

    def new_order(channel, tool, paid, n_lines, ref_amount=None, carrier=False):
        n["order"] += 1
        n["buyer"] += 1
        bid = f"B{n['buyer']:05d}"
        ref = f"{rng.randrange(16**8):08x}@marketplace.amazon.com" if channel == "amazon" else f"user{n['buyer']}"
        _ins(con, "buyers", {"buyer_id": bid, "channel": channel, "native_buyer_ref": ref, "state": "IN"})
        oid = f"O{n['order']:06d}"
        lob = "books" if tool == "cashmonkey" else "general_merch"
        prices = [D(rng.randint(500, 6000)) / 100 for _ in range(n_lines)]
        sub = sum(prices)
        ship = D("3.99") * n_lines
        hand = D("3.00") * n_lines if tool == "upright" else D("0.00")
        tax = (sub * D("0.07")).quantize(D("0.01"))
        if channel == "amazon":
            mfee = sum((p * D("0.15") + D("1.80")).quantize(D("0.01")) for p in prices)
            pfee, label = D("0.00"), D("0.00")
            mkt = f"113-{rng.randrange(10**7):07d}-{rng.randrange(10**7):07d}"
        else:
            mfee = ((sub + ship + hand) * D("0.1325")).quantize(D("0.01"))
            pfee, label = D("0.30"), D("5.50")
            mkt = f"{rng.randrange(10, 100)}-{rng.randrange(10**5):05d}-{rng.randrange(10**5):05d}"
        total = sub + ship + hand + tax
        _ins(con, "orders", {"order_id": oid, "channel": channel, "tool": tool, "marketplace_order_id": mkt,
                             "buyer_id": bid, "paid_at": paid, "item_count": n_lines, "subtotal": sub,
                             "shipping_charged": ship, "handling": hand, "tax": tax, "marketplace_fee": mfee,
                             "payment_fee": pfee, "shipping_label_cost": label, "total": total,
                             "payment_type": "CreditCard"})
        fees = allocate(mfee + pfee, prices)
        for k, p in enumerate(prices):
            iid = new_item(lob, rng.randint(1, 24))
            lid = new_listing(iid, channel, tool, paid - timedelta(days=5), "sold", p, ended=paid)
            _ins(con, "order_lines", {"order_id": oid, "line_no": k + 1, "item_id": iid, "listing_id": lid,
                                      "quantity": 1, "sale_price": p, "fee_alloc": fees[k]})
        labels = D("0.00")
        if carrier:
            _ins(con, "shipping_charges", {"charge_id": f"C{oid}", "carrier": "pitney_bowes", "order_id": oid,
                                           "charged_at": paid + timedelta(hours=3), "amount": label, "tracking": "9400"})
        elif channel == "ebay":
            labels = label
        ledger.append((channel, paid.date(), sub + ship + hand, mfee + pfee, D("0.00"), labels))
        if ref_amount is not None:
            rat = paid + timedelta(days=2, hours=5)
            _ins(con, "refunds", {"refund_id": f"R{oid}", "order_id": oid, "refunded_at": rat,
                                  "amount": ref_amount, "reason": "not as described"})
            ledger.append((channel, rat.date(), D("0.00"), D("0.00"), ref_amount, D("0.00")))
        return oid

    for d in days:
        for _ in range(rng.randint(1, 3)):
            paid = datetime(d.year, d.month, d.day, rng.randrange(24), rng.randrange(60), rng.randrange(60), tzinfo=UTC)
            new_order("amazon", "cashmonkey", paid, rng.choice([1, 1, 2]),
                      ref_amount=D("5.00") if rng.random() < 0.1 else None)
        for tool in ("upright", "cashmonkey"):
            paid = datetime(d.year, d.month, d.day, rng.randrange(24), rng.randrange(60), rng.randrange(60), tzinfo=UTC)
            new_order("ebay", tool, paid, rng.choice([1, 2]), ref_amount=D("7.25") if rng.random() < 0.1 else None,
                      carrier=rng.random() < 0.3)
    new_order("amazon", "cashmonkey", KNOWN_PDT, 1)
    new_order("amazon", "cashmonkey", KNOWN_PST, 1)
    ledger.sort(key=lambda r: r[1])

    # eBay relist chain + active/unsold listings
    iid = new_item("general_merch", 5)
    l1 = new_listing(iid, "ebay", "upright", datetime(2026, 9, 1, tzinfo=UTC), "unsold",
                     D("40.00"), ended=datetime(2026, 9, 8, tzinfo=UTC))
    new_listing(iid, "ebay", "upright", datetime(2026, 9, 9, tzinfo=UTC), "active", D("35.00"), relist_of=l1)
    bk = new_item("books", 7)
    new_listing(bk, "amazon", "cashmonkey", datetime(2026, 9, 2, tzinfo=UTC), "active", D("12.00"))

    def periods(start: date, end: date, step: int, lag: int):
        s = start
        while s + timedelta(days=step - 1) <= end:
            yield s, s + timedelta(days=step - 1), s + timedelta(days=step - 1 + lag)
            s += timedelta(days=step)

    pid = 0
    for channel, step, lag, blocks in (("amazon", 14, 2, [(date(2025, 10, 27), date(2025, 11, 9)),
                                                          (date(2026, 9, 1), date(2026, 9, 28))]),
                                       ("ebay", 1, 1, [(date(2025, 10, 27), date(2025, 11, 9)),
                                                       (date(2026, 9, 1), date(2026, 10, 2))])):
        for b0, b1 in blocks:
            for s, e, paid_on in periods(b0, b1, step, lag):
                rows = [r for r in ledger if r[0] == channel and s <= r[1] <= e]
                g, f, rf, lb = (sum((r[i] for r in rows), D("0.00")) for i in (2, 3, 4, 5))
                pid += 1
                _ins(con, "payouts", {"payout_id": f"P{pid:04d}", "channel": channel, "period_start": s,
                                      "period_end": e, "paid_on": paid_on, "gross": g, "fees": f + lb,
                                      "refunds": rf, "net": g - f - lb - rf})
    con.close()
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("srcs")
    truth = make_truth(d / "_truth.duckdb")
    a = amazon.build(truth, d / "amazon.duckdb")
    e = ebay.build(truth, d / "ebay.duckdb")
    return {"dir": d, "truth": truth, "amazon": d / "amazon.duckdb", "ebay": d / "ebay.duckdb",
            "a_counts": a, "e_counts": e}


def q(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------- helpers
def test_time_and_money_helpers():
    assert pacific_amazon(KNOWN_PDT) == "Sep 30, 2026 4:41:07 PM PDT"
    assert pacific_amazon(KNOWN_PST) == "Nov 5, 2025 9:03:44 AM PST"
    # DST start 2026-03-08 2:00 PST -> 3:00 PDT (10:00 UTC)
    assert pacific_amazon(datetime(2026, 3, 8, 9, 59, 59)) == "Mar 8, 2026 1:59:59 AM PST"
    assert pacific_amazon(datetime(2026, 3, 8, 10, 0, 0)) == "Mar 8, 2026 3:00:00 AM PDT"
    assert pacific_amazon(datetime(2026, 9, 30, 7, 5, 0)) == "Sep 30, 2026 12:05:00 AM PDT"
    assert pacific_text(KNOWN_PDT) == "2026-09-30 16:41:07 PDT"
    assert iso_utc(KNOWN_PDT) == "2026-09-30T23:41:07Z"
    assert iso_utc(datetime(2026, 9, 30, 23, 41, 7, 120000), ms=True) == "2026-09-30T23:41:07.120Z"
    assert money_text(D("45")) == "45.00" and money_text(D("-0.5")) == "-0.50"
    parts = allocate(D("10.00"), [1, 1, 1])
    assert sum(parts) == D("10.00") and parts == [D("3.33"), D("3.33"), D("3.34")]


# ---------------------------------------------------------------- amazon
def test_amazon_quirks(built):
    p = built["amazon"]
    dates = [r[0] for r in q(p, "SELECT purchase_date FROM orders")]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", d) for d in dates)
    times = [r[0] for r in q(p, "SELECT date_time FROM financial_events")]
    assert all(re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2} [AP]M P[SD]T", t) for t in times)
    assert "Sep 30, 2026 4:41:07 PM PDT" in times and "Nov 5, 2025 9:03:44 AM PST" in times
    # PST only between Nov 2 2025 and Mar 8 2026 in this fixture's dates
    assert any(t.endswith("PST") for t in times) and any(t.endswith("PDT") for t in times)
    assert q(p, "SELECT count(*) FROM financial_events WHERE selling_fees > 0 OR other_transaction_fees > 0")[0][0] == 0
    assert q(p, "SELECT count(*) FROM financial_events WHERE type='Order' AND selling_fees < 0")[0][0] > 0
    refunds = q(p, "SELECT product_sales, total FROM financial_events WHERE type='Refund'")
    assert refunds and all(ps < 0 and tot < 0 for ps, tot in refunds)
    assert {r[0] for r in q(p, "SELECT DISTINCT fulfillment_channel FROM orders")} == {"MFN"}
    skus = [r[0] for r in q(p, """SELECT seller_sku FROM order_items WHERE amazon_order_id || '/' || order_item_id
                                  NOT IN (SELECT native_key FROM _dirty_data)""")]
    assert all(re.fullmatch(r"GWM-\d{2}-\d{6}", s) for s in skus)
    opens = [r[0] for r in q(p, "SELECT open_date FROM listings")]
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} P[SD]T", o) for o in opens)
    assert q(p, "SELECT count(*) FROM listings WHERE status='Active' AND quantity=1")[0][0] == 1


def test_amazon_settlements(built):
    p = built["amazon"]
    t = built["truth"]
    truth = q(t, "SELECT period_start, period_end, paid_on, net FROM payouts WHERE channel='amazon' ORDER BY 1")
    nat = q(p, "SELECT settlement_start_date, settlement_end_date, deposit_date, total_amount FROM settlements ORDER BY 1")
    assert truth == nat
    assert all((e - s).days == 13 for s, e, _, _ in nat)
    # events in each settlement (excluding Transfer and the injected duplicate) sum to the deposit
    bad = q(p, """
        SELECT s.settlement_id FROM settlements s JOIN financial_events f USING (settlement_id)
        WHERE f.type <> 'Transfer' AND f.event_id NOT IN (SELECT native_key FROM _dirty_data)
        GROUP BY s.settlement_id, s.total_amount HAVING sum(f.total) <> s.total_amount""")
    assert bad == []
    assert q(p, "SELECT count(*) FROM financial_events WHERE type='Transfer'")[0][0] == len(nat)
    assert built["a_counts"]["_residual_events"] == 0


# ---------------------------------------------------------------- ebay
def test_ebay_quirks(built):
    p = built["ebay"]
    money_cols = {"orders": ["pricingSummary_priceSubtotal", "pricingSummary_deliveryCost", "pricingSummary_tax",
                             "pricingSummary_total", "totalMarketplaceFee"],
                  "line_items": ["lineItemCost", "deliveryCost"], "transactions": ["amount", "totalFeeAmount"],
                  "payouts": ["amount"], "listings": ["price"]}
    for tbl, cols in money_cols.items():
        for c in cols:
            vals = [r[0] for r in q(p, f'SELECT "{c}" FROM {tbl}')]
            assert all(isinstance(v, str) and MONEY_RE.match(v) for v in vals), (tbl, c)
    for tbl, c in [("orders", "creationDate"), ("transactions", "transactionDate"), ("payouts", "payoutDate"),
                   ("listings", "listingStartDate")]:
        assert all(ISO_MS_RE.match(r[0]) for r in q(p, f'SELECT "{c}" FROM {tbl}')), (tbl, c)
    assert {r[0] for r in q(p, "SELECT DISTINCT bankLast4 FROM payouts")} == {"0101"}
    types = {r[0] for r in q(p, "SELECT DISTINCT transactionType FROM transactions")}
    assert {"SALE", "REFUND", "SHIPPING_LABEL"} <= types
    assert q(p, "SELECT count(*) FROM transactions WHERE transactionType <> 'NON_SALE_CHARGE' AND payoutId IS NULL "
                "AND transactionDate BETWEEN '2026-09-01' AND '2026-10-03'")[0][0] == 0  # dates covered by fixture payouts
    clean = [r[0] for r in q(p, "SELECT sku FROM line_items WHERE lineItemId NOT IN (SELECT native_key FROM _dirty_data)")]
    assert all(re.fullmatch(r"(UP|GWM)-\d{2}-\d{6}", s) for s in clean)
    assert any(s.startswith("UP-") for s in clean) and any(s.startswith("GWM-") for s in clean)
    chain = q(p, """SELECT c.listingStatus, p.listingStatus FROM listings c JOIN listings p
                    ON c.relistParentId = p.legacyItemId""")
    assert chain == [("ACTIVE", "ENDED")]
    # orders whose label is billed by an outside carrier get no eBay SHIPPING_LABEL
    t = built["truth"]
    carrier_mkt = {r[0] for r in q(t, "SELECT o.marketplace_order_id FROM shipping_charges s JOIN orders o USING (order_id)")}
    labelled = {r[0] for r in q(p, "SELECT orderId FROM transactions WHERE transactionType='SHIPPING_LABEL'")}
    assert carrier_mkt and not (carrier_mkt & labelled)
    assert built["e_counts"]["_residual_events"] == 0


# ---------------------------------------------------------------- reconciliation
def test_reconciliation(built):
    for rows in (amazon.reconcile(built["truth"], built["amazon"]), ebay.reconcile(built["truth"], built["ebay"])):
        assert rows
        for r in rows:
            assert r["truth_orders"] == r["native_orders"], r
            assert r["truth_subtotal"] == r["native_subtotal"], r
            assert r["truth_payout_net"] == r["native_payout_net"], r
    tools = {r["tool"] for r in ebay.reconcile(built["truth"], built["ebay"])}
    assert tools == {"upright", "cashmonkey"}
    # whole-channel totals including fees (eBay)
    t = q(built["truth"], "SELECT sum(total), sum(marketplace_fee + payment_fee) FROM orders WHERE channel='ebay'")[0]
    n = q(built["ebay"], """SELECT sum(pricingSummary_total::DECIMAL(12,2)), sum(totalMarketplaceFee::DECIMAL(12,2))
                            FROM orders WHERE buyer_username <> 'TEST'""")[0]
    assert t == n


# ---------------------------------------------------------------- dirty data
@pytest.mark.parametrize("src,keycol", [
    ("amazon", {"orders": "amazon_order_id", "order_items": "amazon_order_id || '/' || order_item_id",
                "financial_events": "event_id"}),
    ("ebay", {"orders": "orderId", "line_items": "lineItemId", "transactions": "transactionId"}),
])
def test_dirty_data_listed(built, src, keycol):
    p = built[src]
    dirty = q(p, "SELECT table_name, native_key, kind, note FROM _dirty_data")
    kinds = {d[2] for d in dirty}
    assert {"duplicate_api_row", "lowercase_sku_prefix", "missing_store_code", "test_order"} <= kinds
    for table, key, kind, note in dirty:
        assert note
        assert q(p, f"SELECT count(*) FROM {table} WHERE {keycol[table]} = '{key}'")[0][0] == 1, (table, key)
    tbl, col = ("orders", "buyer_email") if src == "amazon" else ("orders", "buyer_username")
    assert q(p, f"SELECT count(*) FROM {tbl} WHERE {col} = 'TEST'")[0][0] == 1
    sku_tbl, sku_col = ("order_items", "seller_sku") if src == "amazon" else ("line_items", "sku")
    assert q(p, f"SELECT count(*) FROM {sku_tbl} WHERE {sku_col} SIMILAR TO '(up|gwm)-.*'")[0][0] == 1


# ---------------------------------------------------------------- determinism
def _dump(path):
    con = duckdb.connect(str(path), read_only=True)
    out = {}
    for (t,) in con.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall():
        out[t] = con.execute(f'SELECT * FROM "{t}" ORDER BY ALL').fetchall()
    con.close()
    return out


def test_determinism(built, tmp_path):
    for mod in (amazon, ebay):
        again = tmp_path / f"{mod.__name__.rsplit('.', 1)[1]}.duckdb"
        mod.build(built["truth"], again, seed=7)
        assert _dump(again) == _dump(built[mod.__name__.rsplit(".", 1)[1]])
        other = tmp_path / f"other_{again.name}"
        mod.build(built["truth"], other, seed=8)
        assert _dump(other) != _dump(again)
    assert not list(tmp_path.glob("*.tmp"))
