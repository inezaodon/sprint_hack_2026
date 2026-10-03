"""/api/dashboard endpoints against a tiny hand-built harmonized DB and a fake KPI module (Engineer 7)."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from goodwill_pulse.routes import dashboard

# Our own app with just this router: importing goodwill_pulse.api opens data/warehouse.duckdb for writing,
# which fails while the live --reload server holds its lock.
app = FastAPI()
app.include_router(dashboard.router)
PAGE = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"


@app.get("/dashboard", response_class=HTMLResponse)
def _page() -> str:
    return PAGE.read_text()

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "sql" / "harmonized" / "schema.sql"


# ---------------------------------------------------------------- tiny harmonized DB
def build_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    con.execute("""INSERT INTO dim_store VALUES ('Store01','Mishawaka','Mishawaka',true,false),
                                                ('Store02','Elkhart','Elkhart',false,false)""")
    con.execute("""INSERT INTO dim_channel VALUES ('shopgoodwill','ShopGoodwill','ShopGoodwill','upright'),
                                                  ('ebay','eBay','eBay','both')""")
    # Aug + Sept 2026 orders; Oct 2 = month in progress. One order lands at 01:30 UTC Sept 1 = Aug 31 ET.
    orders = [
        ("shopgoodwill:1", "shopgoodwill", "1", "2026-08-31 01:30:00+00", "2026-08-30", 100.00, 10.00, 0),
        ("shopgoodwill:2", "shopgoodwill", "2", "2026-09-01 01:30:00+00", "2026-08-31", 50.00, 5.00, 0),
        ("shopgoodwill:3", "shopgoodwill", "3", "2026-09-10 15:00:00+00", "2026-09-10", 200.00, 20.00, 20.00),
        ("ebay:12-1", "ebay", "12-1", "2026-09-11 15:00:00+00", "2026-09-11", 40.00, 4.00, 0),
        ("ebay:12-2", "ebay", "12-2", "2026-10-02 15:00:00+00", "2026-10-02", 30.00, 3.00, 0),
    ]
    for k, ch, mid, ts, bd, sub, fee, ref in orders:
        con.execute("""INSERT INTO fct_orders VALUES (?,?,?,?, 'general_merch','b',?,?,1,?,0,0,0,?,?,?)""",
                    [k, ch, mid, ch, ts, bd, sub, fee, ref, sub])
    lines = [  # order_key, line, channel, sku, store, category, price, fee, date
        ("shopgoodwill:1", 1, "shopgoodwill", "UP-01-000001", "Store01", "Jewelry", 100.00, 10.00, "2026-08-30"),
        ("shopgoodwill:2", 1, "shopgoodwill", "UP-02-000002", "Store02", "Toys & Games", 50.00, 5.00, "2026-08-31"),
        ("shopgoodwill:3", 1, "shopgoodwill", "UP-01-000003", "Store01", "Jewelry", 150.00, 15.00, "2026-09-10"),
        ("shopgoodwill:3", 2, "shopgoodwill", "UP-02-000004", "Store02", "Electronics", 50.00, 5.00, "2026-09-10"),
        ("ebay:12-1", 1, "ebay", "UP-02-000005", None, "Electronics", 40.00, 4.00, "2026-09-11"),
        ("ebay:12-2", 1, "ebay", "UP-01-000006", "Store01", "Art", 30.00, 3.00, "2026-10-02"),
    ]
    for o, n, ch, sku, st, cat, price, fee, bd in lines:
        con.execute("INSERT INTO fct_order_lines VALUES (?,?,?,?,?,?, 'general_merch','t',1,?,?,?)",
                    [o, n, ch, sku, st, cat, price, fee, bd])
    items = [  # id, store, identified, manifested, first listed
        ("UP-01-000003", "Store01", "2026-09-02 14:00:00+00", "2026-09-03 14:00:00+00", "2026-09-05 14:00:00+00"),
        ("UP-02-000004", "Store02", "2026-08-20 14:00:00+00", "2026-08-25 14:00:00+00", "2026-09-01 14:00:00+00"),
        ("UP-01-000007", "Store01", "2026-09-20 14:00:00+00", "2026-09-21 14:00:00+00", None),  # backlog at month end
        ("UP-01-000008", "Store01", "2026-09-29 14:00:00+00", "2026-10-01 02:00:00+00", None),  # sent Sep 30 ET
    ]
    for i, st, idt, man, lst in items:
        con.execute("""INSERT INTO fct_items VALUES (?,?, 'general_merch','Jewelry',?, 'ai',?,?,NULL,'E001',5)""",
                    [i, st, idt, man, lst])
    con.close()
    return path


# ---------------------------------------------------------------- fake kpi module (contract section 4)
@dataclass
class KpiDef:
    id: str
    label: str
    pillar: str
    unit: str
    anchor: bool
    scorecard: bool
    better: str
    availability: str
    source: str
    formula: str
    fn: object = None


IDS = [  # 15 scorecard KPIs, 3 of them anchors
    ("total_revenue", "growth", "usd", False), ("revenue_growth_yoy", "growth", "pct", False),
    ("net_margin", "profitability", "pct", True), ("revenue_per_labor_hour", "productivity", "usd_per_hour", True),
    ("listings_created", "productivity", "count", False), ("listings_per_employee", "productivity", "count", False),
    ("sales_per_employee", "productivity", "usd", False), ("sell_through", "inventory", "pct", True),
    ("days_donation_to_listing", "inventory", "days", False), ("unlisted_backlog", "inventory", "count", False),
    ("unsold_pct", "inventory", "pct", False), ("average_selling_price", "sales", "usd", False),
    ("top10_categories_revenue", "growth", "usd", False), ("top10_categories_margin", "profitability", "usd", False),
    ("repeat_buyer_rate", "engagement", "pct", False),
]


def make_fake_kpi(none_for: set[str] = frozenset()):
    mod = types.ModuleType("fake_kpi")
    mod.KPIS = {i: KpiDef(i, i.replace("_", " ").title(), p if p != "sales" else "growth", u, a, True, "up",
                          "input" if i == "net_margin" else "built", "fct_orders", "sum of things")
                for i, p, u, a in IDS}
    mod.calls = []

    def compute(con, kpi_id, month, store=None, channel=None):
        mod.calls.append((kpi_id, month, store, channel))
        if kpi_id in none_for:
            return None
        sql = "SELECT sum(subtotal) FROM fct_orders WHERE date_trunc('month', business_date) = ?"
        p = [month]
        if channel:
            sql += " AND channel = ?"; p.append(channel)
        v = con.execute(sql, p).fetchone()[0]
        return float(v) if v is not None else None

    def kpi_card(con, kpi_id, month, store=None, channel=None):
        d = mod.KPIS[kpi_id]
        ly = date(month.year - 1, month.month, 1)
        return {"id": kpi_id, "label": d.label, "pillar": d.pillar, "unit": d.unit,
                "value": compute(con, kpi_id, month, store, channel), "prior_month": None,
                "prior_year": compute(con, kpi_id, ly, store, channel),
                "trend": [{"month": month, "value": compute(con, kpi_id, month, store, channel)}],
                "status": "good", "availability": d.availability, "source": d.source, "formula": d.formula}

    def scorecard(con, month, store=None, channel=None):
        cards = [kpi_card(con, k, month, store, channel) for k, d in mod.KPIS.items() if d.scorecard]
        pillars: dict = {}
        for c in cards:
            pillars.setdefault(c["pillar"], []).append(c)
        return {"anchors": [c for c in cards if mod.KPIS[c["id"]].anchor], "scorecard": cards, "pillars": pillars}

    def series(con, kpi_id, months=13, store=None, channel=None):
        return [{"month": date(2026, 9, 1), "value": compute(con, kpi_id, date(2026, 9, 1), store, channel)}]

    mod.compute, mod.kpi_card, mod.scorecard, mod.series = compute, kpi_card, scorecard, series
    return mod


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "HARMONIZED_PATH", build_db(tmp_path / "harmonized.duckdb"))
    fake = make_fake_kpi()
    monkeypatch.setitem(sys.modules, "fake_kpi_dash", fake)
    monkeypatch.setattr(dashboard, "KPI_MODULE", "fake_kpi_dash")
    c = TestClient(app)
    c.fake = fake
    return c


# ---------------------------------------------------------------- tests
def test_options(client):
    r = client.get("/api/dashboard/options").json()
    assert r["months"] == ["2026-10", "2026-09", "2026-08"]
    assert r["default_month"] == "2026-09"          # October is still in progress
    assert [s["store_id"] for s in r["stores"]] == ["Store01", "Store02"]
    assert {c["channel"] for c in r["channels"]} == {"shopgoodwill", "ebay"}
    assert r["kpi_ready"] and len(r["kpis"]) == 15
    assert set(r["availability"]) == {"built", "input", "n/a"}


def test_kpis_shape_and_rows(client):
    r = client.get("/api/dashboard/kpis?month=2026-09").json()
    assert r["month"] == "2026-09" and r["filters"] == {"store": None, "channel": None}
    assert len(r["anchors"]) == 3 and len(r["scorecard"]) == 15
    assert set(r["pillars"]) >= {"growth", "profitability", "productivity", "inventory", "engagement"}
    assert r["scorecard"][0]["value"] == 240.0          # Sept ET: 200 + 40 (the Sept 1 01:30 UTC order is Aug)
    rows = {x["label"]: x["ids"] for x in r["scorecard_rows"]}
    assert list(rows) == dashboard.SCORECARD_ROWS
    assert sum(len(v) for v in rows.values()) == 15
    assert "net_margin" in rows["Financial"] and "revenue_per_labor_hour" in rows["Productivity"]
    assert "sell_through" in rows["Inventory"] and "average_selling_price" in rows["Sales"]
    assert {"repeat_buyer_rate", "top10_categories_margin"} <= set(rows["Category + Customer"])
    assert r["missing_values"] == []


def test_kpis_default_month_and_filters_pass_through(client):
    r = client.get("/api/dashboard/kpis?channel=ebay&store=Store01").json()
    assert r["month"] == "2026-09" and r["filters"] == {"store": "Store01", "channel": "ebay"}
    assert ("total_revenue", date(2026, 9, 1), "Store01", "ebay") in client.fake.calls
    assert r["scorecard"][0]["value"] == 40.0


def test_missing_value_is_null_not_zero(client, monkeypatch):
    fake = make_fake_kpi(none_for={"sell_through"})
    monkeypatch.setitem(sys.modules, "fake_kpi_dash", fake)
    r = client.get("/api/dashboard/kpis?month=2026-09").json()
    st = next(c for c in r["scorecard"] if c["id"] == "sell_through")
    assert st["value"] is None
    assert r["missing_values"] == ["sell_through"]


def test_bad_inputs(client):
    assert client.get("/api/dashboard/kpis?month=Sept").status_code == 422
    assert client.get("/api/dashboard/kpis?store=Store99").status_code == 422
    assert client.get("/api/dashboard/kpis?channel=etsy").status_code == 422
    assert client.get("/api/dashboard/series?kpi=nope").status_code == 404
    assert client.get("/api/dashboard/categories?by=units").status_code == 422


def test_series(client):
    r = client.get("/api/dashboard/series?kpi=total_revenue&months=3&month=2026-10").json()
    assert [p["month"] for p in r["points"]] == ["2026-08", "2026-09", "2026-10"]
    assert [p["value"] for p in r["points"]] == [150.0, 240.0, 30.0]
    assert r["unit"] == "usd"
    r = client.get("/api/dashboard/series?kpi=total_revenue").json()   # delegates to kpi.series
    assert r["points"] == [{"month": "2026-09", "value": 240.0}]
    r = client.get("/api/dashboard/series?kpi=total_revenue&months=2&month=2026-09&by=channel").json()
    by = {s["key"]: [p["value"] for p in s["points"]] for s in r["series"]}
    assert by == {"shopgoodwill": [150.0, 200.0], "ebay": [None, 40.0]}


def test_stores(client):
    r = client.get("/api/dashboard/stores?month=2026-09").json()
    s = {x["store_id"]: x for x in r["stores"]}
    assert s["Store01"]["revenue"] == 150.0 and s["Store02"]["revenue"] == 50.0
    assert s[None]["store_name"] == "Unattributed" and s[None]["revenue"] == 40.0   # never silently dropped
    assert r["total_revenue"] == 240.0
    assert s["Store01"]["items_sent"] == 3 and s["Store02"]["items_sent"] == 0              # incl. one manifested Oct 1 02:00 UTC = Sept 30 ET
    assert s["Store01"]["items_identified"] == 3 and s["Store02"]["items_identified"] == 0
    assert s["Store01"]["items_listed"] == 1 and s["Store02"]["items_listed"] == 1
    assert s["Store01"]["backlog_end"] == 2 and s["Store02"]["backlog_end"] == 0
    r = client.get("/api/dashboard/stores?month=2026-09&channel=ebay").json()
    assert r["total_revenue"] == 40.0


def test_categories(client):
    r = client.get("/api/dashboard/categories?month=2026-09").json()
    cats = [(c["category"], c["revenue"]) for c in r["categories"]]
    assert cats == [("Jewelry", 150.0), ("Electronics", 90.0)]
    j = r["categories"][0]
    assert j["rank"] == 1 and j["fees"] == 15.0
    assert j["refunds"] == pytest.approx(15.0)             # $20 refund on a $200 order, 150/200 of it
    assert j["gross_profit"] == pytest.approx(120.0) and j["margin_pct"] == pytest.approx(0.8)
    m = client.get("/api/dashboard/categories?month=2026-09&by=margin&store=Store02").json()
    assert [c["category"] for c in m["categories"]] == ["Electronics"]


def test_503_when_db_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "HARMONIZED_PATH", tmp_path / "nope.duckdb")
    r = client.get("/api/dashboard/options")
    assert r.status_code == 503 and "not built yet" in r.json()["detail"]


def test_503_when_kpi_module_missing(client, monkeypatch):
    monkeypatch.setattr(dashboard, "KPI_MODULE", "goodwill_pulse.no_such_kpi_module")
    r = client.get("/api/dashboard/kpis?month=2026-09")
    assert r.status_code == 503 and "KPI module not available" in r.json()["detail"]
    o = client.get("/api/dashboard/options").json()      # options still works
    assert o["kpi_ready"] is False and o["months"]


def test_page_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    html = r.text
    assert html.lstrip().lower().startswith("<!doctype html>")
    for needle in ("/api/dashboard/kpis", "/api/dashboard/stores", "/api/dashboard/categories", 'href="/"', 'href="/close"',
                   "cdnjs.cloudflare.com/ajax/libs/Chart.js"):
        assert needle in html


def test_real_kpi_module_on_tiny_db(tmp_path, monkeypatch):
    """Integration with Engineer 6's goodwill_pulse.kpi (skipped if it does not import)."""
    kpi = pytest.importorskip("goodwill_pulse.kpi")
    monkeypatch.setattr(dashboard, "HARMONIZED_PATH", build_db(tmp_path / "harmonized.duckdb"))
    c = TestClient(app)
    r = c.get("/api/dashboard/kpis?month=2026-09")
    assert r.status_code == 200, r.text
    k = r.json()
    assert [a["id"] for a in k["anchors"]] == list(kpi.ANCHORS)
    assert [x["id"] for x in k["scorecard"]] == list(kpi.SCORECARD)
    rows = {x["label"]: x["ids"] for x in k["scorecard_rows"]}
    assert all(len(v) == 3 for v in rows.values())          # slide 35: 5 rows x 3
    rev = next(x for x in k["scorecard"] if x["id"] == "total_revenue")
    assert rev["value"] == pytest.approx(240.0)
    s = c.get("/api/dashboard/series?kpi=total_revenue&months=2&month=2026-09").json()
    assert [p["month"] for p in s["points"]] == ["2026-08", "2026-09"]
    assert s["points"][1]["value"] == pytest.approx(240.0)
    assert c.get("/api/dashboard/options").json()["kpi_ready"] is True


def test_page_has_ai_brief_hooks():
    html = PAGE.read_text()
    for needle in ('id="ai-brief"', "/api/ai/narrative/month?", "engine: ", "numbers checked ✓",
                   "box.hidden = true"):                          # hides on 503 / any error
        assert needle in html, needle


def test_page_has_ask_the_data_hooks():
    html = PAGE.read_text()
    for needle in ('id="ask-form"', 'id="ask-q"', '"/api/ai/ask"', "question: q, month: state.month",
                   "What was revenue on eBay last month?", "Top categories by revenue last month",
                   "Which stores sent the most items to e-commerce last month?", "tool: ", "filters: "):
        assert needle in html, needle
    assert html.count('class="chip"') == 3
