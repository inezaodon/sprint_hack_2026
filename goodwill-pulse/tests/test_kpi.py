"""KPI tests on a tiny hand-built harmonized DB (sql/harmonized/schema.sql + rows below).

The fixture world (all values hand-computed in comments):

Orders (business_date = NY date of paid_at):
  O9  2025-09-15 shopgoodwill buyer z  subtotal 80 fee 8   line Y1 Store01 Jewelry 80 (fee 8)
  O0  2026-08-15 shopgoodwill buyer a  subtotal 20 fee 2   line P1 Store01 Books   20 (fee 2)
  O1  2026-09-10 shopgoodwill buyer a  subtotal 100 fee 10 lines A1 Store01 Jewelry 60 (6), A2 Store02 Books 40 (4)
  O2  2026-09-12 ebay         buyer b  subtotal 50 fee 5   line A3 Store01 Jewelry 50 (5)
  O3  2026-09-20 ebay         buyer c  subtotal 30 fee 3   line A4 Store02 Books   30 (3)
  O10 2026-10-02 ebay         buyer b  subtotal 10 fee 1   line Z1 Store01 Books   10 (1)   -> data as_of 2026-10-02
Refund R1 on O1, $10, 2026-09-25.
Labor: 2025-09-03 E1 lister 8h $32; 2026-09-01 E1 lister 10h $40; 2026-09-02 E2 shipper 10h $20. (none in Aug 2026)
Inputs: 2025-09 overhead 10; 2026-09 overhead 20, store retail 3420. Budget 2026-09: sgw 100, ebay 100.
Listings (first listings; times 12:00Z = 08:00 NY):
  Y1 sgw 2025-09-01 sold 2025-09-15     Y2 sgw 2025-09-02 ended unsold 2025-09-20
  P1 sgw 2026-08-10 sold 08-15          A3 ebay 2026-08-01 sold 09-12 (42 days -> NOT within 30)
  A1 sgw 2026-09-01 sold 09-10          A2 sgw 2026-09-02 sold 09-10        A4 ebay 2026-09-05 sold 09-20
  A5 sgw 2026-09-03 ended unsold 09-10, relisted (A5r) 2026-09-11, active
Items (identified -> manifested -> first listed):
  A1 08-20 -> 08-25 -> 09-01 (12 d, 7 d)   A2 08-28 -> 08-30 -> 09-02 (5 d, 3 d)
  A4 09-01 -> 09-03 -> 09-05 (4 d, 2 d)    A5 09-01 -> 09-02 -> 09-03 (2 d, 1 d)
  A3 07-25 -> 07-28 -> 08-01   P1 08-01 -> 08-05 -> 08-10   Y1 2025-08-20 -> 2025-08-25 -> 2025-09-01
  B1 Store02 Books manifested 2026-09-15, never listed; B2 Store01 Jewelry manifested 2026-08-10, listed 2026-10-02
"""
from datetime import date
from pathlib import Path

import duckdb
import pytest

from goodwill_pulse import kpi

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "harmonized" / "schema.sql"
SEP, AUG, PY = date(2026, 9, 1), date(2026, 8, 1), date(2025, 9, 1)


def _ts(d, h=12):
    return f"{d} {h:02d}:00:00+00"


ORDERS = [  # key, channel, buyer, paid date, subtotal, fees
    ("sgw:O9", "shopgoodwill", "z", "2025-09-15", 80, 8),
    ("sgw:O0", "shopgoodwill", "a", "2026-08-15", 20, 2),
    ("sgw:O1", "shopgoodwill", "a", "2026-09-10", 100, 10),
    ("ebay:O2", "ebay", "b", "2026-09-12", 50, 5),
    ("ebay:O3", "ebay", "c", "2026-09-20", 30, 3),
    ("ebay:O10", "ebay", "b", "2026-10-02", 10, 1),
]
LINES = [  # order_key, line_no, sku, store, category, price, fee
    ("sgw:O9", 1, "Y1", "Store01", "Jewelry", 80, 8),
    ("sgw:O0", 1, "P1", "Store01", "Books", 20, 2),
    ("sgw:O1", 1, "A1", "Store01", "Jewelry", 60, 6),
    ("sgw:O1", 2, "A2", "Store02", "Books", 40, 4),
    ("ebay:O2", 1, "A3", "Store01", "Jewelry", 50, 5),
    ("ebay:O3", 1, "A4", "Store02", "Books", 30, 3),
    ("ebay:O10", 1, "Z1", "Store01", "Books", 10, 1),
]
LISTINGS = [  # key, channel, sku, store, category, listed, ended, status, relist_of
    ("sgw:Y1", "shopgoodwill", "Y1", "Store01", "Jewelry", "2025-09-01", "2025-09-15", "sold", None),
    ("sgw:Y2", "shopgoodwill", "Y2", "Store01", "Books", "2025-09-02", "2025-09-20", "unsold", None),
    ("sgw:P1", "shopgoodwill", "P1", "Store01", "Books", "2026-08-10", "2026-08-15", "sold", None),
    ("ebay:A3", "ebay", "A3", "Store01", "Jewelry", "2026-08-01", "2026-09-12", "sold", None),
    ("sgw:A1", "shopgoodwill", "A1", "Store01", "Jewelry", "2026-09-01", "2026-09-10", "sold", None),
    ("sgw:A2", "shopgoodwill", "A2", "Store02", "Books", "2026-09-02", "2026-09-10", "sold", None),
    ("ebay:A4", "ebay", "A4", "Store02", "Books", "2026-09-05", "2026-09-20", "sold", None),
    ("sgw:A5", "shopgoodwill", "A5", "Store01", "Jewelry", "2026-09-03", "2026-09-10", "unsold", None),
    ("sgw:A5r", "shopgoodwill", "A5", "Store01", "Jewelry", "2026-09-11", None, "active", "sgw:A5"),
]
ITEMS = [  # item, store, category, identified, manifested, first_listed
    ("A1", "Store01", "Jewelry", "2026-08-20", "2026-08-25", "2026-09-01"),
    ("A2", "Store02", "Books", "2026-08-28", "2026-08-30", "2026-09-02"),
    ("A4", "Store02", "Books", "2026-09-01", "2026-09-03", "2026-09-05"),
    ("A5", "Store01", "Jewelry", "2026-09-01", "2026-09-02", "2026-09-03"),
    ("A3", "Store01", "Jewelry", "2026-07-25", "2026-07-28", "2026-08-01"),
    ("P1", "Store01", "Books", "2026-08-01", "2026-08-05", "2026-08-10"),
    ("Y1", "Store01", "Jewelry", "2025-08-20", "2025-08-25", "2025-09-01"),
    ("B1", "Store02", "Books", "2026-09-14", "2026-09-15", None),
    ("B2", "Store01", "Jewelry", "2026-08-09", "2026-08-10", "2026-10-02"),
]


def build_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    for s in ("Store01", "Store02"):
        con.execute("INSERT INTO dim_store VALUES (?, ?, 'X', false, false)", [s, s])
    for k, ch, buyer, d, sub, fee in ORDERS:
        con.execute("INSERT INTO fct_orders VALUES (?,?,?,?,NULL,?,?,?,1,?,0,0,0,?,0,?)",
                    [k, ch, k.split(":")[1], ch, f"{ch}|{buyer}", _ts(d, 16), d, sub, fee, sub])
    for k, n, sku, st, cat, price, fee in LINES:
        ch = "ebay" if k.startswith("ebay") else "shopgoodwill"
        d = next(o[3] for o in ORDERS if o[0] == k)
        con.execute("INSERT INTO fct_order_lines VALUES (?,?,?,?,?,?,NULL,'t',1,?,?,?)",
                    [k, n, ch, sku, st, cat, price, fee, d])
    con.execute("INSERT INTO fct_refunds VALUES ('sgw:R1','sgw:O1','shopgoodwill',?, '2026-09-25', 10)",
                [_ts("2026-09-25", 16)])
    for k, ch, sku, st, cat, listed, ended, status, rel in LISTINGS:
        con.execute("INSERT INTO fct_listings VALUES (?,?,?,?,?,NULL,?,?,?,10,?)",
                    [k, ch, sku, st, cat, _ts(listed), _ts(ended) if ended else None, status, rel])
    for it, st, cat, ide, man, fl in ITEMS:
        con.execute("INSERT INTO fct_items VALUES (?,?,NULL,?,?, 'ai', ?, ?, NULL, 'E1', 5)",
                    [it, st, cat, _ts(ide), _ts(man), _ts(fl) if fl else None])
    con.execute("""INSERT INTO fct_labor VALUES ('E1','lister','2025-09-03',8,32,5),
                   ('E1','lister','2026-09-01',10,40,4), ('E2','shipper','2026-09-02',10,20,0)""")
    con.execute("INSERT INTO fct_monthly_inputs VALUES ('2025-09-01',10,NULL), ('2026-09-01',20,3420)")
    con.execute("INSERT INTO fct_budget VALUES ('2026-09-01','shopgoodwill',100), ('2026-09-01','ebay',100)")
    con.close()
    return path


@pytest.fixture
def db(tmp_path):
    return build_db(tmp_path / "harmonized.duckdb")


@pytest.fixture
def con(db):
    c = kpi.connect_harmonized(db)
    yield c
    c.close()


A = pytest.approx

# Sept 2026, no filter. revenue 180, fees 18, refunds 10 -> gross profit 152; labor 20 h $60 (lister 10 h);
# overhead 20 -> net profit 72.
EXPECTED_SEP = {
    "total_revenue": 180.0,
    "revenue_growth_yoy": 1.25,                 # (180 - 80) / 80
    "budget_attainment": 0.9,                   # 180 / 200
    "ecom_share_of_retail": 0.05,               # 180 / (180 + 3420)
    "net_margin": 0.4,                          # 72 / 180
    "gross_margin": 0.8444,                     # 152 / 180
    "profit_per_labor_hour": 4.6,               # (152 - 60) / 20
    "top_categories_by_margin": 93.0,           # Jewelry 110 - 11 fees - 6 (60 % of R1)
    "revenue_per_labor_hour": 9.0,              # 180 / 20
    "listings_created": 4.0,                    # A1 A2 A4 A5
    "listings_per_day": 2.0,                    # 4 / 2 worked days
    "listings_per_employee": 69.33,             # 4 / (10 / 173.333)
    "sales_per_employee": 1560.0,               # 180 / (20 / 173.333)
    "avg_time_to_list": 3.25,                   # (7 + 3 + 2 + 1) / 4
    "items_identified": 3.0,                    # A4 A5 (09-01), B1 (09-14)
    "items_sent": 3.0,                          # A4 A5 B1
    "sell_through": 0.75,                       # A1 A2 A4 sold <= 30 d of 4
    "days_donation_to_listing": 5.75,           # (12 + 5 + 4 + 2) / 4
    "unlisted_backlog": 2.0,                    # B1, B2 at 2026-09-30
    "unsold_pct": 0.2,                          # ended in Sep: A1 A2 A3 A4 sold, A5 unsold
    "days_to_sell": 18.67,                      # (9.1667 + 8.1667 + 42.1667 + 15.1667) / 4
    "relisted_pct": 0.25,                       # skus started in Sep: A1 A2 A4 A5; A5 relisted
    "avg_selling_price": 45.0,                  # 180 / 4
    "median_sale_price": 45.0,                  # median(30, 40, 50, 60)
    "top_categories_by_revenue": 110.0,         # Jewelry 60 + 50
    "repeat_buyer_rate": 0.3333,                # a (ordered in Aug) of a b c
    "buyers": 3.0,
    "new_buyers": 2.0,                          # b, c
    "refund_rate": 0.0556,                      # 10 / 180
    "customer_satisfaction": None,
    "net_promoter_score": None,
    "marketplace_conversion": None,
}


def test_registry_complete():
    assert set(EXPECTED_SEP) == set(kpi.KPIS)
    assert len([k for k in kpi.KPIS.values() if k.scorecard]) == 15
    assert {k.id for k in kpi.KPIS.values() if k.anchor} == {"net_margin", "revenue_per_labor_hour",
                                                             "sell_through"}
    for k in kpi.KPIS.values():
        assert k.pillar in kpi.PILLARS and k.availability in ("built", "input", "n/a")
        assert k.unit in ("usd", "pct", "count", "days", "usd_per_hour") and k.better in ("up", "down")
        assert k.formula and k.source
    assert {k.id for k in kpi.KPIS.values() if k.availability == "n/a"} == {
        "customer_satisfaction", "net_promoter_score", "marketplace_conversion"}


@pytest.mark.parametrize("kpi_id", sorted(EXPECTED_SEP))
def test_every_kpi_sept(con, kpi_id):
    exp = EXPECTED_SEP[kpi_id]
    got = kpi.compute(con, kpi_id, SEP)
    if exp is None:
        assert got is None
    else:
        assert got == A(exp, abs=0.006)


def test_store_filter(con):
    # Store01 Sep: lines A1 60 + A3 50 = 110, fees 11, refund 10 x 60/100 = 6 -> GP 93.
    # Labor share = Store01 first listings (A1, A5) / 4 = 0.5 -> $30, 10 h. Overhead 20 x 110/180 = 12.2222.
    assert kpi.compute(con, "total_revenue", SEP, store="Store01") == 110.0
    assert kpi.compute(con, "net_margin", SEP, store="Store01") == A((93 - 30 - 20 * 110 / 180) / 110, abs=1e-4)
    assert kpi.compute(con, "revenue_per_labor_hour", SEP, store="Store01") == 11.0
    assert kpi.compute(con, "sell_through", SEP, store="Store01") == 0.5      # A1 sold, A5 not
    assert kpi.compute(con, "sell_through", SEP, store="Store02") == 1.0      # A2, A4
    assert kpi.compute(con, "avg_selling_price", SEP, store="Store01") == 55.0
    assert kpi.compute(con, "unlisted_backlog", SEP, store="Store02") == 1.0  # B1
    assert kpi.compute(con, "items_identified", SEP, store="Store02") == 2.0  # A4, B1
    assert kpi.compute(con, "repeat_buyer_rate", SEP, store="Store02") == 0.5  # buyers a (repeat), c
    assert kpi.compute(con, "refund_rate", SEP, store="Store02") == A(4 / 70, abs=1e-4)
    # store has no budget / store-retail dimension -> None, never 0
    assert kpi.compute(con, "budget_attainment", SEP, store="Store01") is None
    assert kpi.compute(con, "ecom_share_of_retail", SEP, store="Store01") is None
    # a store with no sales in a covered month is a real 0
    assert kpi.compute(con, "total_revenue", SEP, store="Store24") == 0.0


def test_channel_filter(con):
    # eBay Sep: O2 + O3 = 80, fees 8, no refunds -> GP 72. First listings: A4 only -> labor share 1/4 -> $15, 5 h.
    assert kpi.compute(con, "total_revenue", SEP, channel="ebay") == 80.0
    assert kpi.compute(con, "net_margin", SEP, channel="ebay") == A((72 - 15 - 20 * 80 / 180) / 80, abs=1e-4)
    assert kpi.compute(con, "revenue_per_labor_hour", SEP, channel="ebay") == 16.0
    assert kpi.compute(con, "sell_through", SEP, channel="shopgoodwill") == A(2 / 3, abs=1e-4)
    assert kpi.compute(con, "budget_attainment", SEP, channel="ebay") == 0.8
    assert kpi.compute(con, "buyers", SEP, channel="ebay") == 2.0
    assert kpi.compute(con, "unsold_pct", SEP, channel="ebay") == 0.0          # A3, A4 sold
    # unlisted inventory has no channel -> None
    assert kpi.compute(con, "unlisted_backlog", SEP, channel="ebay") is None
    assert kpi.compute(con, "items_sent", SEP, channel="ebay") is None


def test_category_filter_and_breakdown(con):
    assert kpi.compute(con, "total_revenue", SEP, category="Books") == 70.0
    b = kpi.breakdown(con, "total_revenue", SEP, "channel")
    assert b == [{"key": "shopgoodwill", "value": 100.0}, {"key": "ebay", "value": 80.0}]
    top = kpi.breakdown(con, "top_categories_by_margin", SEP, "category")
    assert top == [{"key": "Jewelry", "value": 93.0}, {"key": "Books", "value": 59.0}]  # 70 - 7 - 4


def test_missing_data_is_none(con, tmp_path):
    # Aug 2026: orders exist but no labor -> labor KPIs None (not 0)
    assert kpi.compute(con, "total_revenue", AUG) == 20.0
    assert kpi.compute(con, "revenue_per_labor_hour", AUG) is None
    assert kpi.compute(con, "net_margin", AUG) is None                  # no labor, no overhead
    # a month with no orders at all
    assert kpi.compute(con, "total_revenue", date(2026, 3, 1)) is None
    assert kpi.compute(con, "buyers", date(2026, 3, 1)) is None
    # a month with no prior year -> growth None
    assert kpi.compute(con, "revenue_growth_yoy", AUG) is None
    # empty DB
    empty = tmp_path / "empty.duckdb"
    c = duckdb.connect(str(empty)); c.execute(SCHEMA.read_text()); c.close()
    ec = kpi.connect_harmonized(empty)
    for k in kpi.KPIS:
        assert kpi.compute(ec, k, SEP) is None, k
    sc = kpi.scorecard(ec, SEP)
    assert all(c["value"] is None and c["status"] is None for c in sc["scorecard"])
    ec.close()


def test_card_prior_month_prior_year_and_status(con):
    c = kpi.kpi_card(con, "net_margin", SEP)
    assert c["value"] == 0.4
    assert c["prior_year"] == 0.375                    # (80 - 8 - 32 - 10) / 80
    assert c["prior_month"] is None                    # Aug: no labor
    assert c["status"] == "good"                       # 0.40 > 0.375
    for key in ("id", "label", "pillar", "unit", "value", "prior_month", "prior_year", "trend", "status",
                "availability", "source", "formula"):
        assert key in c
    r = kpi.kpi_card(con, "total_revenue", SEP)
    assert (r["value"], r["prior_month"], r["prior_year"]) == (180.0, 20.0, 80.0)
    assert r["status"] == "good" and r["provisional"] is False
    rplh = kpi.kpi_card(con, "revenue_per_labor_hour", SEP)
    assert (rplh["prior_year"], rplh["status"]) == (10.0, "bad")      # 9 vs 10 = -10 %
    st = kpi.kpi_card(con, "sell_through", SEP)
    assert st["status"] == "good" and st["provisional"] is True       # 30-day window not closed by 10-02
    assert st["prior_year"] == 0.5 and st["prior_month"] == 0.5      # Y1/Y2; P1 sold, A3 sold after 42 d
    un = kpi.kpi_card(con, "unsold_pct", SEP)
    assert (un["prior_year"], un["status"]) == (0.5, "good")          # lower is better
    oct_card = kpi.kpi_card(con, "total_revenue", date(2026, 10, 1))
    assert oct_card["provisional"] is True and oct_card["value"] == 10.0
    top = kpi.kpi_card(con, "top_categories_by_revenue", SEP)
    assert top["breakdown"][0] == {"key": "Jewelry", "value": 110.0}
    na = kpi.kpi_card(con, "net_promoter_score", SEP)
    assert na["value"] is None and na["status"] is None and na["availability"] == "n/a"


def test_series_13_points(con):
    s = kpi.series(con, "total_revenue", end=SEP)
    assert len(s) == 13 and s[0]["month"] == "2025-09-01" and s[-1]["month"] == "2026-09-01"
    assert [p["value"] for p in s] == [80.0] + [None] * 10 + [20.0, 180.0]
    assert kpi.series(con, "total_revenue")[-1] == {"month": "2026-10-01", "value": 10.0}  # default end
    assert len(kpi.series(con, "sell_through", months=6, store="Store01", end=SEP)) == 6
    card = kpi.kpi_card(con, "total_revenue", SEP)
    assert card["trend"] == s


def test_scorecard_shape(con):
    sc = kpi.scorecard(con, SEP)
    assert [c["id"] for c in sc["anchors"]] == ["net_margin", "revenue_per_labor_hour", "sell_through"]
    assert [c["id"] for c in sc["scorecard"]] == list(kpi.SCORECARD)
    assert set(sc["pillars"]) == set(kpi.PILLARS)
    assert sum(len(v) for v in sc["pillars"].values()) == len(kpi.KPIS)
    vals = {c["id"]: c["value"] for c in sc["scorecard"]}
    assert vals["total_revenue"] == 180.0 and vals["sell_through"] == 0.75
    filtered = kpi.scorecard(con, SEP, store="Store01", channel="shopgoodwill")
    # Store01 on SGW: line A1 60 only
    assert next(c for c in filtered["scorecard"] if c["id"] == "total_revenue")["value"] == 60.0


def test_shipping_labels_reduce_margin(db):
    c = duckdb.connect(str(db))
    c.execute("INSERT INTO fct_fees VALUES ('f1','ebay:O2','ebay','shipping_label',5,'2026-09-12')")
    c.close()
    con = kpi.connect_harmonized(db)
    assert kpi.compute(con, "gross_margin", SEP) == A(147 / 180, abs=1e-4)
    assert kpi.compute(con, "net_margin", SEP) == A(67 / 180, abs=1e-4)
    assert kpi.compute(con, "gross_margin", SEP, store="Store02") == A((70 - 7 - 4) / 70, abs=1e-4)  # O2 is Store01
    con.close()


def test_new_york_month_boundary(db):
    # a listing at 2026-09-01 02:00Z is 2026-08-31 22:00 in New York -> August cohort
    c = duckdb.connect(str(db))
    c.execute("INSERT INTO fct_listings VALUES ('sgw:N1','shopgoodwill','N1','Store01','Art',NULL,"
              "'2026-09-01 02:00:00+00',NULL,'active',5,NULL)")
    c.close()
    con = kpi.connect_harmonized(db)
    assert kpi.compute(con, "listings_created", AUG) == 3.0      # A3, P1, N1
    assert kpi.compute(con, "listings_created", SEP) == 4.0
    con.close()


def test_scorecard_order_slide35(con):
    assert kpi.SCORECARD == (
        "total_revenue", "revenue_growth_yoy", "net_margin",
        "listings_created", "revenue_per_labor_hour", "listings_per_employee",
        "days_donation_to_listing", "unlisted_backlog", "unsold_pct",
        "avg_selling_price", "sell_through", "sales_per_employee",
        "top_categories_by_revenue", "top_categories_by_margin", "repeat_buyer_rate")
    rows = {c["id"]: c["scorecard_row"] for c in kpi.scorecard(con, SEP)["scorecard"]}
    assert rows["sell_through"] == "Sales" and rows["unsold_pct"] == "Inventory"
    assert rows["listings_created"] == "Productivity" and rows["repeat_buyer_rate"] == "Category + Customer"


def test_first_month_history_kpis_are_none(con):
    # 2025-09 is the first month with orders: buyer z can't be classed as new or repeat
    assert kpi.compute(con, "buyers", PY) == 1.0
    assert kpi.compute(con, "repeat_buyer_rate", PY) is None
    assert kpi.compute(con, "new_buyers", PY) is None
    assert kpi.compute(con, "revenue_growth_yoy", PY) is None
    c = kpi.kpi_card(con, "repeat_buyer_rate", SEP)
    assert c["value"] == A(0.3333, abs=1e-4) and c["prior_year"] is None and c["status"] is None
    assert c["trend"][0] == {"month": "2025-09-01", "value": None}
    assert kpi.kpi_card(con, "repeat_buyer_rate", PY)["status"] is None
