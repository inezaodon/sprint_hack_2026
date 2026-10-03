"""ShopGoodwill / GoodwillFinds / Goodwillbooks native DBs built from a tiny hand-made truth world.

Checks: native quirks (Pacific naive times, first-line shipping, ET offsets by DST, cents, US date text),
reconciliation of clean rows to truth, statements/payouts = sum of the period's native activity,
documented dirty rows, determinism and atomic writes.
"""
from __future__ import annotations

import random
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from goodwill_pulse.sources import goodwillbooks, goodwillfinds, shopgoodwill

PT, ET = ZoneInfo("America/Los_Angeles"), ZoneInfo("America/New_York")
D = lambda x: Decimal(str(x)).quantize(Decimal("0.01"))  # noqa: E731

try:  # Engineer 1's DDL when available, else the contract section 1 minimum
    from goodwill_pulse.gen.truth import TRUTH_DDL  # type: ignore
except Exception:  # noqa: BLE001
    TRUTH_DDL = """
    CREATE TABLE stores (store_id VARCHAR, store_name VARCHAR, city VARCHAR, ai_flagging BOOLEAN, ecom_eye BOOLEAN);
    CREATE TABLE items (item_id VARCHAR PRIMARY KEY, store_id VARCHAR, line_of_business VARCHAR, category VARCHAR,
        title VARCHAR, isbn VARCHAR, identified_at TIMESTAMPTZ, flagged_by VARCHAR, manifest_id VARCHAR,
        manifested_at TIMESTAMPTZ, posted_at TIMESTAMPTZ, poster_id VARCHAR, list_minutes DOUBLE);
    CREATE TABLE listings (listing_id VARCHAR PRIMARY KEY, item_id VARCHAR, channel VARCHAR, tool VARCHAR,
        native_listing_id VARCHAR, listed_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, status VARCHAR,
        price DECIMAL(12,2), relist_of VARCHAR);
    CREATE TABLE buyers (buyer_id VARCHAR PRIMARY KEY, channel VARCHAR, native_buyer_ref VARCHAR, state VARCHAR);
    CREATE TABLE orders (order_id VARCHAR PRIMARY KEY, channel VARCHAR, tool VARCHAR, marketplace_order_id VARCHAR,
        upright_order_id VARCHAR, buyer_id VARCHAR, paid_at TIMESTAMPTZ, item_count INTEGER,
        subtotal DECIMAL(12,2), shipping_charged DECIMAL(12,2), handling DECIMAL(12,2), tax DECIMAL(12,2),
        marketplace_fee DECIMAL(12,2), payment_fee DECIMAL(12,2), shipping_label_cost DECIMAL(12,2),
        total DECIMAL(12,2), payment_type VARCHAR);
    CREATE TABLE order_lines (order_id VARCHAR, line_no INTEGER, item_id VARCHAR, listing_id VARCHAR,
        quantity INTEGER, sale_price DECIMAL(12,2), fee_alloc DECIMAL(12,2));
    CREATE TABLE refunds (refund_id VARCHAR PRIMARY KEY, order_id VARCHAR, refunded_at TIMESTAMPTZ,
        amount DECIMAL(12,2), reason VARCHAR);
    CREATE TABLE payouts (payout_id VARCHAR PRIMARY KEY, channel VARCHAR, period_start DATE, period_end DATE,
        paid_on DATE, gross DECIMAL(12,2), fees DECIMAL(12,2), refunds DECIMAL(12,2), net DECIMAL(12,2));
    """

CATS = ["Jewelry", "Home Decor", "Toys & Games", "Clothing", "Watches"]
CH = {  # channel: (tool, sku prefix, n orders, local tz used for its payout periods)
    "shopgoodwill": ("upright", "UP", 40, ET),
    "goodwillfinds": ("upright", "UP", 25, ET),
    "goodwillbooks": ("cashmonkey", "GWM", 25, ET),
}
# fixed edge-case timestamps (UTC): DST in Jan, Pacific/Eastern month-end split, period boundary 10/11
EDGES = [datetime(2026, 1, 15, 17, 5, tzinfo=timezone.utc),
         datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc),      # Sep 30 22:30 PT, Oct 1 01:30 ET
         datetime(2026, 9, 11, 6, 30, tzinfo=timezone.utc),      # Sep 10 23:30 PT = Sep 11 ET (SGW period 2)
         datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc)]       # just before spring-forward in ET


def _period(ch: str, d: date) -> tuple[date, date, date]:
    if ch == "shopgoodwill":
        s = d.replace(day=1 if d.day <= 10 else 11 if d.day <= 20 else 21)
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        e = s + timedelta(days=9) if s.day < 21 else nxt - timedelta(days=1)
        return s, e, e + timedelta(days=2)
    if ch == "goodwillfinds":
        s = d - timedelta(days=d.weekday())
        return s, s + timedelta(days=6), s + timedelta(days=9)
    s = d.replace(day=1)
    nxt = (s + timedelta(days=32)).replace(day=1)
    return s, nxt - timedelta(days=1), nxt.replace(day=15)


def make_truth(path: Path, seed: int = 1) -> Path:
    rng = random.Random(seed)
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone='UTC'")
    con.execute(TRUTH_DDL)
    ins = lambda t, row: con.execute(  # noqa: E731
        f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    for s in ("Store03", "Store07"):
        ins("stores", dict(store_id=s, store_name=f"Goodwill {s}", city="South Bend", ai_flagging=True, ecom_eye=False))
    pay = {}
    n_item = n_ref = 0
    for ch, (tool, prefix, n, tz) in CH.items():
        nat = 4_000_000 if ch == "shopgoodwill" else 6_000_000
        for k in range(n):
            paid = EDGES[k] if k < len(EDGES) else datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(
                minutes=rng.randrange(30 * 24 * 60))
            buyer = f"{ch}-b{k % 7}"
            if not con.execute("SELECT 1 FROM buyers WHERE buyer_id=?", [buyer]).fetchone():
                ins("buyers", dict(buyer_id=buyer, channel=ch, state=rng.choice(["IN", "MI", "OH"]),
                                   native_buyer_ref=f"{1000 + k % 7}" if ch != "goodwillbooks" else f"CUST{k % 7}"))
            lines = []
            for ln in range(1, (3 if k % 5 == 0 else 1) + 1):
                n_item += 1
                store = rng.choice(["Store03", "Store07"])
                item = f"{prefix}-{store[-2:]}-{n_item:06d}"
                cat = "Books" if ch == "goodwillbooks" else rng.choice(CATS)
                ins("items", dict(item_id=item, store_id=store, category=cat, title=f"{cat} thing {n_item}",
                                  line_of_business="books" if ch == "goodwillbooks" else "general_merch",
                                  isbn=f"978{n_item:010d}" if ch == "goodwillbooks" else None))
                price = D(rng.uniform(10, 120))
                nat += 1
                listed = paid - timedelta(days=rng.randint(2, 20))
                relist = None
                if k % 6 == 1:  # relist chain: first listing ended unsold
                    first = f"L{n_item}a"
                    ins("listings", dict(listing_id=first, item_id=item, channel=ch, tool=tool,
                                         native_listing_id=str(nat) if ch != "goodwillbooks" else item,
                                         listed_at=listed - timedelta(days=10), ended_at=listed - timedelta(hours=1),
                                         status="unsold", price=price + 10))
                    relist, nat = first, nat + 1
                lid = f"L{n_item}"
                ins("listings", dict(listing_id=lid, item_id=item, channel=ch, tool=tool,
                                     native_listing_id=str(nat) if ch != "goodwillbooks" else item,
                                     listed_at=listed, ended_at=paid, status="sold", price=price, relist_of=relist))
                lines.append((ln, item, lid, price))
            sub = sum(p for *_, p in lines)
            ship = D(rng.choice([0, 5.99, 8.5]))
            hand = D(3 * len(lines)) if tool == "upright" else D(0)
            tax = D(sub * D("0.07"))
            mfee, pfee = D(sub * D("0.12")), D(sub * D("0.03") + D("0.30"))
            oid = f"{ch}-o{k}"
            mid = {"shopgoodwill": str(90_000_000 + k), "goodwillfinds": str(5_500_000_000 + k),
                   "goodwillbooks": f"GWB{300000 + k}"}[ch]
            ins("orders", dict(order_id=oid, channel=ch, tool=tool, marketplace_order_id=mid, buyer_id=buyer,
                               paid_at=paid, item_count=len(lines), subtotal=sub, shipping_charged=ship,
                               handling=hand, tax=tax, marketplace_fee=mfee, payment_fee=pfee,
                               shipping_label_cost=D(6), total=sub + ship + hand + tax, payment_type="CreditCard"))
            allocs = [D(mfee * p / sub) for *_, p in lines]
            allocs[-1] = mfee - sum(allocs[:-1])
            for (ln, item, lid, price), fa in zip(lines, allocs):
                ins("order_lines", dict(order_id=oid, line_no=ln, item_id=item, listing_id=lid, quantity=1,
                                        sale_price=price, fee_alloc=fa))
            p = _period(ch, paid.astimezone(tz).date())
            g = pay.setdefault((ch, p), [D(0)] * 3)
            g[0] += sub + ship + hand
            g[1] += mfee + pfee
            refs = []
            if k % 4 == 0:      # full refund, two hours later
                refs.append((paid + timedelta(hours=2), sub + ship + hand))   # refunds exclude tax
            elif k % 4 == 1:    # partial refunds; a second one 20 days later (other period / month)
                refs += [(paid + timedelta(hours=26), D(sub / 3)), (paid + timedelta(days=20), D(5))]
            for when, amt in refs:
                n_ref += 1
                ins("refunds", dict(refund_id=f"R{n_ref}", order_id=oid, refunded_at=when, amount=amt, reason="damaged"))
                pr = _period(ch, when.astimezone(tz).date())
                pay.setdefault((ch, pr), [D(0)] * 3)[2] += amt
    # one active and one unsold-only listing per channel
    for ch, (tool, prefix, *_rest) in CH.items():
        for st in ("active", "unsold"):
            n_item += 1
            item = f"{prefix}-03-{n_item:06d}"
            ins("items", dict(item_id=item, store_id="Store03", category="Books" if prefix == "GWM" else "Art",
                              title=f"lonely {n_item}", line_of_business="books" if prefix == "GWM" else "general_merch"))
            ins("listings", dict(listing_id=f"L{n_item}", item_id=item, channel=ch, tool=tool,
                                 native_listing_id=item if ch == "goodwillbooks" else str(7_000_000 + n_item),
                                 listed_at=datetime(2026, 9, 20, 15, tzinfo=timezone.utc),
                                 ended_at=None if st == "active" else datetime(2026, 9, 27, 15, tzinfo=timezone.utc),
                                 status=st, price=D(25)))
    for i, ((ch, (s, e, paid_on)), (g, f, r)) in enumerate(sorted(pay.items())):
        ins("payouts", dict(payout_id=f"P{i}", channel=ch, period_start=s, period_end=e, paid_on=paid_on,
                            gross=g, fees=f, refunds=r, net=g - f - r))
    con.close()
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("gw")
    truth = make_truth(d / "_truth.duckdb")
    out = {}
    for mod in (shopgoodwill, goodwillfinds, goodwillbooks):
        name = mod.__name__.rsplit(".", 1)[1]
        counts = mod.build(truth, d / f"{name}.duckdb", seed=7)
        out[name] = (d / f"{name}.duckdb", counts)
    tcon = duckdb.connect(str(truth), read_only=True)
    tcon.execute("SET TimeZone='UTC'")
    yield {"truth": tcon, "dir": d, "truth_path": truth, **out}
    tcon.close()


def q(path: Path, sql: str, params=None):
    with duckdb.connect(str(path), read_only=True) as c:
        return c.execute(sql, params or []).fetchall()


def truth_totals(tcon, ch: str) -> dict:
    r = tcon.execute("""SELECT count(*), sum(subtotal), sum(shipping_charged + handling), sum(tax),
                               sum(marketplace_fee + payment_fee), sum(item_count)
                        FROM orders WHERE channel = ?""", [ch]).fetchone()
    ref = tcon.execute("SELECT coalesce(sum(amount),0) FROM refunds JOIN orders USING (order_id) WHERE channel=?",
                       [ch]).fetchone()[0]
    return dict(orders=r[0], subtotal=r[1], ship=r[2], tax=r[3], fees=r[4], items=r[5], refunds=ref)


# ---------------------------------------------------------------- generic
def test_build_counts_and_atomic(built):
    for name in ("shopgoodwill", "goodwillfinds", "goodwillbooks"):
        path, counts = built[name]
        assert path.exists() and not path.with_name(path.name + ".tmp").exists()
        assert counts["_dirty_data"] >= 3 and all(v >= 0 for v in counts.values())
    assert built["shopgoodwill"][1]["sales"] == 40 + 8 * 2 + 1   # 8 three-item orders, + 1 TEST row


def test_dirty_rows_documented_and_present(built):
    checks = {
        "shopgoodwill": {"seller_fees": "FeeID = ?", "auctions": "ItemID = ?",
                         "sales": "OrderID || '/' || ItemID = ?"},
        "goodwillfinds": {"line_items": "id = ?", "orders": "id = ?"},
        "goodwillbooks": {"sales_order_lines": "order_no || '/' || line = ?", "sales_orders": "order_no = ?"},
    }
    for name, where in checks.items():
        path = built[name][0]
        rows = q(path, "SELECT table_name, native_key, kind, note FROM _dirty_data")
        kinds = {r[2] for r in rows}
        assert {"duplicate_row", "test_order"} <= kinds, name
        for table, key, kind, note in rows:
            assert note and q(path, f"SELECT count(*) FROM {table} WHERE {where[table]}", [key])[0][0] == 1, (table, key)
    sgw, gf, gwb = (built[n][0] for n in ("shopgoodwill", "goodwillfinds", "goodwillbooks"))
    assert q(sgw, "SELECT count(*) FROM auctions WHERE SellerItemCode LIKE 'up-%'")[0][0] == 1
    assert q(sgw, "SELECT count(*) FROM sales WHERE BuyerID = 'TEST'")[0][0] == 1
    # the TEST auction gets an ItemID above every real auction (incl. later unsold/open ones)
    assert q(sgw, "SELECT max(ItemID) FROM auctions")[0][0] == q(
        sgw, "SELECT ItemID FROM auctions WHERE SellerItemCode = 'UP-03-999999'")[0][0]
    assert q(gf, "SELECT count(*) FROM line_items WHERE vendor = 'Goodwil Michiana #07'")[0][0] == 1
    assert q(gwb, "SELECT count(*) FROM sales_order_lines WHERE store_code IS NULL")[0][0] == 1
    assert q(gwb, "SELECT count(*) FROM sales_orders WHERE customer_ref = 'TEST'")[0][0] == 1


def test_determinism(built, tmp_path):
    for mod in (shopgoodwill, goodwillfinds, goodwillbooks):
        name = mod.__name__.rsplit(".", 1)[1]
        mod.build(built["truth_path"], tmp_path / f"{name}.duckdb", seed=7)
        a, b = built[name][0], tmp_path / f"{name}.duckdb"
        for t in q(a, "SELECT table_name FROM duckdb_tables()"):
            sql = f"SELECT * FROM {t[0]} ORDER BY ALL"
            assert q(a, sql) == q(b, sql), (name, t[0])


def test_missing_truth_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        shopgoodwill.build(tmp_path / "nope.duckdb", tmp_path / "x.duckdb")
    assert not (tmp_path / "x.duckdb").exists()


# ---------------------------------------------------------------- ShopGoodwill
def test_sgw_quirks(built):
    sgw, t = built["shopgoodwill"][0], built["truth"]
    # Pacific naive PaidDate
    paid = q(sgw, "SELECT PaidDate FROM sales WHERE OrderID = 90000001")[0][0]
    assert paid == datetime(2026, 9, 30, 22, 30) and paid.tzinfo is None
    # shipping + handling on first line only; handling $3/item for the whole order
    rows = q(sgw, "SELECT ShippingCharged, Handling FROM sales WHERE OrderID = 90000000 ORDER BY ItemID")
    assert len(rows) == 3 and rows[0][1] == D(9) and all(r == (0, 0) for r in rows[1:])
    # fees: Commission per item + one PaymentProcessing per order, total = truth fees
    clean = "FeeID NOT IN (SELECT CAST(native_key AS BIGINT) FROM _dirty_data WHERE table_name='seller_fees')"
    assert q(sgw, f"SELECT sum(Amount) FROM seller_fees WHERE {clean}")[0][0] == truth_totals(t, "shopgoodwill")["fees"]
    assert q(sgw, "SELECT count(*) FROM seller_fees WHERE FeeType='PaymentProcessing'")[0][0] == 40
    # relist chain + status mapping
    rel = q(sgw, "SELECT a.Status, b.Status FROM auctions a JOIN auctions b ON b.ItemID = a.RelistOfItemID")
    assert rel and all(r == ("Sold", "Unsold") for r in rel)
    assert {r[0] for r in q(sgw, "SELECT DISTINCT Status FROM auctions")} == {"Open", "Sold", "Unsold"}
    assert q(sgw, "SELECT count(*) FROM auctions WHERE Status='Sold' AND (HighBid < StartingBid OR NumBids < 1)")[0][0] == 0
    # statements: period 1/2/3 boundaries; the Sep 10 23:30 PT sale (02:30 ET Sep 11) belongs to period 2
    st = q(sgw, "SELECT Period, day(PeriodStart), PeriodEnd, RemitDate FROM periodic_statements")
    for per, d0, end, remit in st:
        assert d0 == {1: 1, 2: 11, 3: 21}[per] and remit > end
    assert q(sgw, "SELECT PaidDate FROM sales WHERE OrderID = 90000002")[0][0].day == 10
    p2 = q(sgw, "SELECT GrossSales FROM periodic_statements WHERE Year=2026 AND Month=9 AND Period=2")[0][0]
    assert p2 > 0


def test_sgw_reconciles(built):
    sgw, tt = built["shopgoodwill"][0], truth_totals(built["truth"], "shopgoodwill")
    r = q(sgw, """SELECT count(DISTINCT OrderID), sum(HammerPrice), sum(ShippingCharged + Handling), sum(SalesTax),
                         sum(RefundAmount), count(*) FROM sales WHERE BuyerID <> 'TEST'""")[0]
    assert r == (tt["orders"], tt["subtotal"], tt["ship"], tt["tax"], tt["refunds"], tt["items"])
    # statements equal the period's native activity (ET business dates), apart from multi-refund orders whose
    # second refund is shown inline under the latest date: compare gross + fees exactly, refunds in total
    act = dict(((y, m, p), (g, c, pf)) for y, m, p, g, c, pf in q(sgw, """
        SELECT year(d), month(d), CASE WHEN day(d)<=10 THEN 1 WHEN day(d)<=20 THEN 2 ELSE 3 END,
               sum(gross), sum(commission), sum(payment)
        FROM (""" + shopgoodwill._period_activity_sql().replace("FROM sales", "FROM sales WHERE BuyerID <> 'TEST'")
            .replace("FROM seller_fees", "FROM seller_fees WHERE FeeID NOT IN (SELECT CAST(native_key AS BIGINT) "
                     "FROM _dirty_data WHERE table_name='seller_fees')").replace("WHERE BuyerID <> 'TEST' WHERE Refunded",
                                                                             "WHERE BuyerID <> 'TEST' AND Refunded")
        + ") GROUP BY ALL"))
    st = q(sgw, "SELECT Year, Month, Period, GrossSales, Commission, PaymentFees, Refunds, NetRemit FROM periodic_statements")
    for y, m, p, g, c, pf, ref, net in st:
        assert act.get((y, m, p), (0, 0, 0)) == (g, c, pf), (y, m, p)
        assert net == g - c - pf - ref
    assert sum(r[6] for r in st) == tt["refunds"]


# ---------------------------------------------------------------- GoodwillFinds
def test_gf_quirks(built):
    gf = built["goodwillfinds"][0]
    created = dict(q(gf, "SELECT id, created_at FROM orders"))
    assert created[5500000000] == "2026-01-15T12:05:00-05:00"          # EST in January
    assert created[5500000001] == "2026-10-01T01:30:00-04:00"           # EDT; Oct 1 in Eastern
    assert created[5500000003] == "2026-03-08T01:59:00-05:00"           # last minute of EST
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d-0[45]:00", v) for v in created.values() if v)
    vendors = {r[0] for r in q(gf, "SELECT DISTINCT vendor FROM line_items WHERE sku NOT LIKE '%999999'")}
    assert vendors <= {"Goodwill Michiana #03", "Goodwill Michiana #07", "Goodwil Michiana #07"}
    pts = [r[0] for r in q(gf, "SELECT product_type FROM line_items WHERE sku NOT LIKE '%999999' "
                                "UNION ALL SELECT product_type FROM products")]
    assert {p.lower() for p in pts} <= {c.lower() for c in CATS + ["Art"]}
    assert any(p.islower() for p in pts) and any(p.isupper() for p in pts) and any(p in CATS for p in pts)
    assert {r[0] for r in q(gf, "SELECT DISTINCT financial_status FROM orders")} == {
        "paid", "refunded", "partially_refunded"}
    assert q(gf, "SELECT name FROM orders WHERE id = 5500000002")[0][0].startswith("#GF")
    assert {r[0] for r in q(gf, "SELECT status FROM products")} == {"active", "sold", "archived"}


def test_gf_reconciles(built):
    gf, tt = built["goodwillfinds"][0], truth_totals(built["truth"], "goodwillfinds")
    r = q(gf, """SELECT count(*), sum(subtotal_price), sum(total_shipping_price), sum(total_tax), sum(marketplace_fee)
                 FROM orders WHERE email_hash <> 'TEST'""")[0]
    assert r == (tt["orders"], tt["subtotal"], tt["ship"], tt["tax"], tt["fees"])
    clean = ("id NOT IN (SELECT CAST(native_key AS BIGINT) FROM _dirty_data WHERE table_name='line_items' "
             "AND kind IN ('duplicate_row', 'test_order'))")
    assert q(gf, f"SELECT sum(price), count(*) FROM line_items WHERE {clean}")[0] == (tt["subtotal"], tt["items"])
    assert q(gf, "SELECT sum(amount) FROM refunds")[0][0] == tt["refunds"]
    # weekly payouts = that week's native activity (Eastern dates from the offset text)
    act = dict(q(gf, """
        WITH a AS (SELECT CAST(substr(created_at,1,10) AS DATE) d, subtotal_price + total_shipping_price g,
                          marketplace_fee f, 0 r FROM orders WHERE email_hash <> 'TEST'
                   UNION ALL SELECT CAST(substr(created_at,1,10) AS DATE), 0, 0, amount FROM refunds)
        SELECT CAST(date_trunc('week', d) AS DATE), [sum(g), sum(f), sum(r)] FROM a GROUP BY 1"""))
    for date_, amount, g, f, rf in q(gf, "SELECT date, amount, charges_gross, fees, refunds_gross FROM payouts"):
        assert act[date_ - timedelta(days=9)] == [g, f, rf] and amount == g - f - rf


# ---------------------------------------------------------------- Goodwillbooks
def test_gwb_quirks(built):
    gwb = built["goodwillbooks"][0]
    d = dict(q(gwb, "SELECT order_no, order_date FROM sales_orders"))
    assert d["GWB300000"] == "01/15/2026 12:05" and d["GWB300001"] == "10/01/2026 01:30"
    assert all(re.fullmatch(r"\d\d/\d\d/\d{4} \d\d:\d\d", v) for v in d.values())
    types = dict(q(gwb, "SELECT column_name, data_type FROM information_schema.columns WHERE column_name LIKE '%cents'"))
    assert set(types.values()) == {"BIGINT"}
    codes = {r[0] for r in q(gwb, "SELECT DISTINCT store_code FROM sales_order_lines WHERE store_code IS NOT NULL")}
    assert codes <= {"03", "07"}
    for month, paid_on in q(gwb, "SELECT statement_month, paid_on FROM monthly_statements"):
        assert paid_on.strftime("%Y-%m") > month
    assert {r[0] for r in q(gwb, "SELECT status FROM sales_orders")} == {"COMPLETE", "REFUNDED", "PARTIAL_REFUND"}
    assert {r[0] for r in q(gwb, "SELECT status FROM inventory")} == {"LISTED", "SOLD", "DELISTED"}
    assert q(gwb, "SELECT count(*), count(DISTINCT sku) FROM inventory")[0] == (37, 37)  # relists collapse per SKU


def test_gwb_reconciles(built):
    gwb, tt = built["goodwillbooks"][0], truth_totals(built["truth"], "goodwillbooks")
    c = lambda x: int(x * 100)  # noqa: E731
    r = q(gwb, """SELECT count(*), sum(items_cents), sum(shipping_cents), sum(tax_cents), sum(fee_cents), sum(refund_cents)
                  FROM sales_orders WHERE customer_ref <> 'TEST'""")[0]
    assert r == (tt["orders"], c(tt["subtotal"]), c(tt["ship"]), c(tt["tax"]), c(tt["fees"]), c(tt["refunds"]))
    dup = "order_no || '/' || line NOT IN (SELECT native_key FROM _dirty_data WHERE kind IN ('duplicate_row','test_order'))"
    assert q(gwb, f"SELECT sum(unit_price_cents * qty), count(*) FROM sales_order_lines WHERE {dup}")[0] == (
        c(tt["subtotal"]), tt["items"])
    # monthly statement gross/fees = the month's native orders (Eastern month from the text date)
    act = dict(q(gwb, """SELECT substr(order_date,7,4) || '-' || substr(order_date,1,2), [sum(items_cents + shipping_cents),
                         sum(fee_cents)] FROM sales_orders WHERE customer_ref <> 'TEST' GROUP BY 1"""))
    st = q(gwb, "SELECT statement_month, gross_cents, fees_cents, refunds_cents, net_cents FROM monthly_statements")
    for m, g, f, rf, net in st:
        assert act[m] == [g, f] and net == g - f - rf
    assert sum(s[3] for s in st) == c(tt["refunds"])


def test_fallback_statements_without_truth_payouts(built, tmp_path):
    """If truth has no payouts for a channel, statements are derived from native activity and still balance."""
    import shutil
    truth = tmp_path / "_truth.duckdb"
    shutil.copy(built["truth_path"], truth)
    with duckdb.connect(str(truth)) as c:
        c.execute("DELETE FROM payouts")
    sgw = tmp_path / "sgw.duckdb"
    shopgoodwill.build(truth, sgw)
    st = q(sgw, "SELECT Period, PeriodStart, PeriodEnd, GrossSales, Commission, PaymentFees, Refunds, NetRemit "
                "FROM periodic_statements")
    assert st and all(net == g - c - pf - r for _, _, _, g, c, pf, r, net in st)
    assert all(s.day == {1: 1, 2: 11, 3: 21}[p] and e >= s for p, s, e, *_ in st)
    tt = truth_totals(built["truth"], "shopgoodwill")
    assert sum(r[3] for r in st) == tt["subtotal"] + tt["ship"]   # dirty rows are injected after statements
    gf, gwb = tmp_path / "gf.duckdb", tmp_path / "gwb.duckdb"
    goodwillfinds.build(truth, gf)
    goodwillbooks.build(truth, gwb)
    tg = truth_totals(built["truth"], "goodwillfinds")
    assert q(gf, "SELECT sum(charges_gross), sum(refunds_gross) FROM payouts")[0] == (tg["subtotal"] + tg["ship"], tg["refunds"])
    tb = truth_totals(built["truth"], "goodwillbooks")
    assert q(gwb, "SELECT sum(gross_cents), sum(net_cents) FROM monthly_statements")[0] == (
        int((tb["subtotal"] + tb["ship"]) * 100), int((tb["subtotal"] + tb["ship"] - tb["fees"] - tb["refunds"]) * 100))
