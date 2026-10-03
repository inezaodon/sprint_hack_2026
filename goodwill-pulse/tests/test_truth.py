"""Invariants of the synthetic truth world (gen/truth.py) and the ops source DB built from it.

A small world (Sept 1-20 2026, plus the simulated warm-up) is built once into tmp. The full default world's
calibration check is slow-ish (~3 s) and runs only with GP_SLOW=1.

Built-in realism documented here (downstream checks rely on it):
  * jewelry: high price / low volume (highest average price, fewer items than clothing)
  * books: high volume / low price
  * AI-flagging stores identify more items, and most of their items are flagged by AI
  * backlog: items manifested but never posted exist (and grow over the year: slow test)
  * relists: ~15% of unsold general-merch listings are relisted
  * repeat buyers exist; refunds ~2.5% of orders
  * the same eBay account sells both tools' items (ebay orders with tool upright AND cashmonkey)
  * minimum online price threshold: general merch listings >= $10
"""
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from goodwill_pulse.gen import truth
from goodwill_pulse.sources import ops

ET = ZoneInfo("America/New_York")
START, END = date(2026, 9, 1), date(2026, 9, 20)
CUTOFF = datetime(2026, 9, 20, 22, tzinfo=ET)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("truth") / "_truth.duckdb"
    counts = truth.build(path, start=START, end=END, seed=7)
    con = duckdb.connect(str(path), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    yield path, counts, con
    con.close()


def q(con, sql):
    return con.execute(sql).fetchall()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def test_every_table_built_and_nonempty(world):
    _, counts, con = world
    assert set(counts) == set(truth.TABLES)
    for t in truth.TABLES:
        assert counts[t] > 0, t
        assert one(con, f"SELECT count(*) FROM {t}") == counts[t]


def test_order_lines_were_listed_on_that_channel_before_paid(world):
    _, _, con = world
    bad = one(con, """
        SELECT count(*) FROM order_lines ol JOIN orders o USING (order_id)
        LEFT JOIN listings l ON l.listing_id = ol.listing_id
        WHERE l.listing_id IS NULL OR l.item_id <> ol.item_id OR l.channel <> o.channel OR l.tool <> o.tool
           OR l.listed_at >= o.paid_at OR l.status <> 'sold' OR l.ended_at <> o.paid_at""")
    assert bad == 0
    # every sold listing has exactly one order line
    assert one(con, """SELECT count(*) FROM listings l LEFT JOIN order_lines ol USING (listing_id)
                       WHERE l.status = 'sold' AND ol.listing_id IS NULL""") == 0


def test_item_sells_at_most_once_and_posted_before_listed(world):
    _, _, con = world
    assert one(con, "SELECT max(n) FROM (SELECT count(*) n FROM order_lines GROUP BY item_id)") == 1
    assert one(con, "SELECT max(n) FROM (SELECT count(*) n FROM listings WHERE status='sold' GROUP BY item_id)") == 1
    assert one(con, """SELECT count(*) FROM listings l JOIN items i USING (item_id)
                       WHERE i.posted_at IS NULL OR l.listed_at < i.posted_at""") == 0
    assert one(con, """SELECT count(*) FROM items WHERE manifested_at <= identified_at
                       OR posted_at <= manifested_at""") == 0
    # first listing is at posted_at; a relist starts after its parent ended
    assert one(con, """SELECT count(*) FROM items i WHERE posted_at IS NOT NULL AND posted_at <>
                       (SELECT min(listed_at) FROM listings l WHERE l.item_id = i.item_id)""") == 0
    assert one(con, """SELECT count(*) FROM listings r JOIN listings p ON r.relist_of = p.listing_id
                       WHERE p.status <> 'unsold' OR r.listed_at <= p.ended_at OR r.item_id <> p.item_id""") == 0


def test_order_money_adds_up(world):
    _, _, con = world
    assert one(con, """SELECT count(*) FROM orders o JOIN (SELECT order_id, sum(sale_price) s, count(*) n,
                       sum(fee_alloc) f FROM order_lines GROUP BY 1) l USING (order_id)
                       WHERE o.subtotal <> l.s OR o.item_count <> l.n OR l.f <> o.marketplace_fee + o.payment_fee""") == 0
    assert one(con, "SELECT count(*) FROM orders WHERE total <> subtotal + shipping_charged + handling + tax") == 0
    assert one(con, """SELECT count(*) FROM orders WHERE handling <> CASE WHEN tool = 'upright'
                       THEN 3 * item_count ELSE 0 END""") == 0
    assert one(con, "SELECT count(*) FROM orders o LEFT JOIN order_lines USING (order_id) WHERE line_no IS NULL") == 0
    assert one(con, "SELECT count(*) FROM refunds r JOIN orders o USING (order_id) WHERE r.amount > o.total "
                    "OR r.amount <= 0 OR r.refunded_at <= o.paid_at") == 0


def test_payouts_net_and_match_orders(world):
    _, _, con = world
    assert one(con, "SELECT count(*) FROM payouts WHERE net <> gross - fees - refunds") == 0
    # recompute each payout's gross / fees / refunds from orders and refunds by Eastern business date
    bad = one(con, """
        WITH o AS (SELECT channel, (paid_at AT TIME ZONE 'America/New_York')::DATE d,
                          subtotal + shipping_charged + handling g, marketplace_fee + payment_fee f FROM orders),
             lb AS (SELECT (charged_at AT TIME ZONE 'America/New_York')::DATE d, amount FROM shipping_charges
                    WHERE carrier = 'ebay'),
             r AS (SELECT o.channel, (r.refunded_at AT TIME ZONE 'America/New_York')::DATE d, r.amount
                   FROM refunds r JOIN orders o USING (order_id))
        SELECT count(*) FROM payouts p
        WHERE p.gross <> coalesce((SELECT sum(g) FROM o WHERE o.channel = p.channel
                                   AND o.d BETWEEN p.period_start AND p.period_end), 0)
           OR p.fees <> coalesce((SELECT sum(f) FROM o WHERE o.channel = p.channel
                                  AND o.d BETWEEN p.period_start AND p.period_end), 0)
                     + CASE WHEN p.channel = 'ebay' THEN coalesce((SELECT sum(amount) FROM lb
                            WHERE lb.d BETWEEN p.period_start AND p.period_end), 0) ELSE 0 END
           OR p.refunds <> coalesce((SELECT sum(amount) FROM r WHERE r.channel = p.channel
                                     AND r.d BETWEEN p.period_start AND p.period_end), 0)""")
    assert bad == 0
    assert one(con, f"SELECT max(paid_on) FROM payouts") <= END
    # Goodwillbooks pays monthly: no complete month in this 20-day world
    assert {r[0] for r in q(con, "SELECT DISTINCT channel FROM payouts")} == set(truth.CHANNELS) - {"goodwillbooks"}
    # cadences
    assert one(con, "SELECT count(*) FROM payouts WHERE channel='ebay' AND period_start <> period_end") == 0
    assert one(con, "SELECT count(*) FROM payouts WHERE channel='goodwillfinds' AND dayofweek(period_start) <> 1") == 0


def test_budget_and_monthly_inputs_cover_months(world):
    _, _, con = world
    months = [r[0] for r in q(con, "SELECT DISTINCT month FROM budget ORDER BY 1")]
    assert months[0] == date(2026, 9, 1) and months[-1] == date(2026, 12, 1) and len(months) == 4
    assert one(con, "SELECT count(*) FROM budget") == len(months) * len(truth.CHANNELS)
    assert one(con, "SELECT count(*) FROM budget WHERE revenue_budget <= 0") == 0
    assert one(con, "SELECT count(*) FROM monthly_inputs") >= 1


def test_sku_and_id_formats(world):
    _, _, con = world
    for item_id, lob, store in q(con, "SELECT item_id, line_of_business, store_id FROM items"):
        pat = r"UP-(\d{2})-\d{6}" if lob == "general_merch" else r"GWM-(\d{2})-\d{6}"
        m = re.fullmatch(pat, item_id)
        assert m and store == f"Store{m.group(1)}", item_id
    assert one(con, "SELECT count(*) FROM stores") == 24
    assert one(con, "SELECT count(*) FROM stores WHERE ai_flagging") == 9
    assert one(con, "SELECT count(*) FROM stores WHERE ecom_eye") == 2
    ids = dict(q(con, "SELECT channel, any_value(marketplace_order_id) FROM orders GROUP BY 1"))
    assert re.fullmatch(r"\d{2}-\d{5}-\d{5}", ids["ebay"])
    assert re.fullmatch(r"11\d-\d{7}-\d{7}", ids["amazon"])
    assert re.fullmatch(r"GWB\d{6}", ids["goodwillbooks"])
    assert ids["shopgoodwill"].isdigit() and ids["goodwillfinds"].isdigit()
    assert one(con, "SELECT count(*) FROM orders WHERE (tool = 'upright') <> (upright_order_id IS NOT NULL)") == 0
    assert one(con, "SELECT count(DISTINCT marketplace_order_id) FROM orders") == one(con, "SELECT count(*) FROM orders")
    assert one(con, "SELECT count(*) FROM listings WHERE channel IN ('amazon','goodwillbooks') "
                    "AND native_listing_id <> item_id") == 0
    assert one(con, """SELECT count(*) FROM shipping_charges s JOIN orders o USING (order_id)
                       WHERE (s.carrier = 'fedex' AND o.tool = 'cashmonkey') OR (s.carrier = 'ebay' AND o.channel <> 'ebay')""") == 0
    share = one(con, """SELECT avg((s.carrier = 'ebay')::INT) FROM orders o JOIN shipping_charges s USING (order_id)
                        WHERE o.channel = 'ebay' AND s.amount > 0""")
    assert 0.45 < share < 0.75


def test_nothing_after_cutoff_and_labor_consistent(world):
    _, _, con = world
    for t, c in [("orders", "paid_at"), ("refunds", "refunded_at"), ("listings", "listed_at"),
                 ("listings", "ended_at"), ("items", "posted_at"), ("items", "identified_at"),
                 ("shipping_charges", "charged_at")]:
        assert one(con, f"SELECT max({c}) FROM {t}") < CUTOFF, (t, c)
    assert one(con, "SELECT min(paid_at) FROM orders") >= datetime(2026, 9, 1, tzinfo=ET)
    # whoever posted an item worked that day
    assert one(con, """SELECT count(*) FROM productivity p LEFT JOIN labor l USING (employee_id, work_date)
                       WHERE l.hours IS NULL""") == 0
    assert one(con, """SELECT count(*) FROM items i LEFT JOIN labor l ON l.employee_id = i.poster_id
                         AND l.work_date = (i.posted_at AT TIME ZONE 'America/New_York')::DATE
                       WHERE i.posted_at >= DATE '2026-09-02' AND l.hours IS NULL""") == 0
    assert one(con, """SELECT sum(posted) FROM productivity""") == one(con, """SELECT count(*) FROM items
                       WHERE (posted_at AT TIME ZONE 'America/New_York')::DATE >= DATE '2026-09-01'""")


def test_built_in_realism(world):
    _, _, con = world
    asp = dict(q(con, """SELECT i.category, avg(ol.sale_price) FROM order_lines ol JOIN items i USING (item_id)
                         GROUP BY 1"""))
    assert max(asp, key=asp.get) == "Jewelry"
    assert asp["Books"] < min(v for k, v in asp.items() if k != "Books")
    vol = dict(q(con, "SELECT category, count(*) FROM items GROUP BY 1"))
    assert vol["Jewelry"] < vol["Clothing"] and vol["Books"] > vol["Jewelry"]
    per_store = dict(q(con, """SELECT s.ai_flagging, count(*) / count(DISTINCT i.store_id)
                               FROM items i JOIN stores s USING (store_id) GROUP BY 1"""))
    assert per_store[True] > per_store[False]
    assert one(con, """SELECT avg((flagged_by = 'ai')::INT) FROM items JOIN stores USING (store_id)
                       WHERE ai_flagging""") > 0.6
    assert one(con, "SELECT count(*) FROM items WHERE posted_at IS NULL") > 1000            # backlog
    relist = one(con, """SELECT count(r.listing_id) / count(*) FROM listings l
                         LEFT JOIN listings r ON r.relist_of = l.listing_id
                         WHERE l.status = 'unsold' AND l.tool = 'upright'""")
    assert 0.10 < relist < 0.20
    assert one(con, "SELECT min(price) FROM listings WHERE tool = 'upright'") >= 10
    assert one(con, "SELECT count(DISTINCT tool) FROM orders WHERE channel = 'ebay'") == 2
    rate = one(con, "SELECT (SELECT count(*) FROM refunds) / count(*) FROM orders")
    assert 0.01 < rate < 0.04
    assert one(con, "SELECT max(n) FROM (SELECT count(*) n FROM orders GROUP BY buyer_id)") > 1   # repeat buyers
    assert one(con, "SELECT count(*) FROM orders o LEFT JOIN buyers b USING (buyer_id) "
                    "WHERE b.buyer_id IS NULL OR b.channel <> o.channel") == 0


def test_deterministic_for_a_seed():
    a = truth.generate(START, date(2026, 9, 5), seed=11)
    b = truth.generate(START, date(2026, 9, 5), seed=11)
    c = truth.generate(START, date(2026, 9, 5), seed=12)
    for t in truth.TABLES:
        assert a[t].equals(b[t]), t
    assert not a["orders"].equals(c["orders"])


def test_ops_db_from_truth(world, tmp_path):
    path, counts, _ = world
    out = tmp_path / "ops.duckdb"
    got = ops.build(path, out)
    assert got["upright_items"] == counts["items"]
    assert got["timeclock"] == counts["labor"]
    assert got["operational_productivity"] == counts["productivity"]
    assert got["budget"] == counts["budget"] and got["stores"] == 24
    con = duckdb.connect(str(out), read_only=True)
    assert con.execute("SELECT count(*) FROM upright_items WHERE posted_at IS NULL").fetchone()[0] > 0
    con.close()


@pytest.mark.skipif(not os.environ.get("GP_SLOW"), reason="full-world calibration: set GP_SLOW=1")
def test_full_world_calibration(tmp_path):
    path = tmp_path / "_truth.duckdb"
    truth.build(path)
    rep = truth.calibration_report(path)
    pac = rep["upright_2026_09_30_pacific"].iloc[0]
    assert 115 <= pac["orders"] <= 141 and 11_900 <= float(pac["subtotal"]) <= 14_600
    k = rep["kpis_by_month"].set_index("month")
    full = k.iloc[1:-1]                      # skip the first (no repeat history) and partial last month
    assert full["rev_per_labor_hour"].between(40, 90).all()
    assert full["net_margin"].between(0.10, 0.30 + 0.01).all()
    assert k["repeat_buyer_rate"].iloc[-2] > 0.2
    st = rep["sell_through_30d_by_cohort"].iloc[:-2]
    assert st["gm"].between(0.45, 0.61).all()
    backlog = rep["backlog_month_end"]["backlog"]
    assert backlog.iloc[-1] > 2 * backlog.iloc[0]
    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("""SELECT count(*) FROM shipping_charges WHERE carrier='fedex' AND amount = -42.10
                          AND (charged_at AT TIME ZONE 'America/New_York')::DATE = DATE '2026-10-01'""").fetchone()[0] == 1
    con.close()
