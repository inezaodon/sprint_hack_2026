"""Export the app's views as JSON documents for the published artifact's database.

    goodwill-pulse/.venv/bin/python tools/export_artifact_docs.py <out_dir>             # everything
    goodwill-pulse/.venv/bin/python tools/export_artifact_docs.py <out_dir> --hub-only  # only hub/*, storeday/*, supro/*,
                                                     # thriftly/all (reads pulse/all and quality/latest already in <out_dir>)

Writes <collection>__<doc_id>.json files (each under 240 KB), ready to load with ArtifactData
batch writes. Sources: the FastAPI routes (pulse, dashboard, close, quality) and two queries on harmonized.duckdb
(daily totals by marketplace, and per store x category pipeline by month).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "goodwill-pulse"
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from goodwill_pulse.api import app  # noqa: E402
from goodwill_pulse.config import DATA_DIR  # noqa: E402

TZ = "America/New_York"


MAX_DOC = 240_000


def put_doc(out: Path, coll: str, doc_id: str, obj) -> None:
    s = json.dumps(obj, default=str, separators=(",", ":"))
    assert len(s) < MAX_DOC, (coll, doc_id, len(s))
    (out / f"{coll}__{doc_id}.json").write_text(s)
    print(f"{coll}/{doc_id}: {len(s):,} bytes")


def main(out: Path, hub_only: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if hub_only:
        export_hub(out, json.loads((out / "pulse__all.json").read_text()), json.loads((out / "quality__latest.json").read_text()))
        return
    c = TestClient(app)

    def get(path):
        r = c.get(path)
        if r.status_code != 200:
            raise SystemExit(f"{path}: {r.status_code} {r.text[:200]}")
        return r.json()

    def put(coll, doc_id, obj):
        put_doc(out, coll, doc_id, obj)

    opt = get("/api/dashboard/options")
    months = opt["months"]
    close_months = get("/api/close/months")
    with_data = [m for m in close_months["months"][:4]]
    put("site", "options", {**{k: opt[k] for k in ("months", "default_month", "data_through", "stores", "channels",
                                                  "pillars", "availability", "kpis")},
                            "close_months": with_data, "close_default": close_months["default"]})
    series_ids = None
    for m in months:
        k = get(f"/api/dashboard/kpis?month={m}")
        cards = {}
        for card in k["anchors"] + k["scorecard"] + [x for v in k["pillars"].values() for x in v]:
            cards.setdefault(card["id"], card)
        series_ids = series_ids or list(cards)
        put("month", m, {"month": m, "cards": cards, "anchors": [x["id"] for x in k["anchors"]],
                         "scorecard_rows": k["scorecard_rows"], "pillars": {p: [x["id"] for x in v] for p, v in k["pillars"].items()},
                         "stores": get(f"/api/dashboard/stores?month={m}"),
                         "categories": {b: get(f"/api/dashboard/categories?month={m}&by={b}") for b in ("revenue", "margin")}})
    dm = opt["default_month"]
    put("series", "all", {i: get(f"/api/dashboard/series?kpi={i}&months=13&month={dm}&by=channel") for i in series_ids})
    pulse = {d: get(f"/api/pulse?d={d}") for d in get("/api/pulse/dates")[:21]}
    put("pulse", "all", pulse)
    for m in with_data:
        put("close", m["month"], get(f"/api/close/run?month={m['month']}"))
    quality = get("/api/close/quality")
    put("quality", "latest", quality)

    con = duckdb.connect(str(DATA_DIR / "harmonized.duckdb"), read_only=True)
    daily = con.execute("""SELECT business_date, channel, count(*), round(sum(subtotal), 2),
                                  round(sum(coalesce(shipping_charged, 0)), 2), round(sum(coalesce(marketplace_fees, 0)), 2),
                                  round(sum(coalesce(refund_amount, 0)), 2)
                           FROM fct_orders GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    put("daily", "all", {"cols": ["date", "channel", "orders", "item_sales", "shipping", "fees", "refunds"],
                         "rows": [[str(d), ch, n, float(a), float(s), float(f), float(r)] for d, ch, n, a, s, f, r in daily]})
    names = dict(con.execute("SELECT store_id, store_name FROM dim_store").fetchall())
    rows = con.execute(f"""
        WITH ev AS (
          SELECT strftime(timezone('{TZ}', identified_at_utc), '%Y-%m') m, store_id, category, 1 id, 0 se, 0 li, 0 su, 0.0 rv FROM fct_items WHERE identified_at_utc IS NOT NULL
          UNION ALL SELECT strftime(timezone('{TZ}', manifested_at_utc), '%Y-%m'), store_id, category, 0, 1, 0, 0, 0.0 FROM fct_items WHERE manifested_at_utc IS NOT NULL
          UNION ALL SELECT strftime(timezone('{TZ}', first_listed_at_utc), '%Y-%m'), store_id, category, 0, 0, 1, 0, 0.0 FROM fct_items WHERE first_listed_at_utc IS NOT NULL
          UNION ALL SELECT strftime(business_date, '%Y-%m'), store_id, category, 0, 0, 0, quantity, sale_price FROM fct_order_lines)
        SELECT m, coalesce(store_id, ''), coalesce(category, ''), sum(id), sum(se), sum(li), sum(su), round(sum(rv), 2)
        FROM ev WHERE m >= '{months[-1]}' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
    put("storecat", "all", {"cols": ["month", "store", "category", "identified", "sent", "listed", "sold", "revenue"],
                            "stores": {k: v for k, v in names.items()},
                            "rows": [[m, s, c_, int(i), int(se), int(li), int(su), float(rv)] for m, s, c_, i, se, li, su, rv in rows]})
    con.close()
    export_hub(out, pulse, quality)


# ------------------------------------------------------------------------------------------------- Revenue Hub docs
# Source of an order: Amazon and Goodwillbooks come through Cash Monkey; ShopGoodwill and GoodwillFinds through Upright;
# eBay is listed by both, split by line of business (books -> Cash Monkey, general merchandise -> Upright).
SOURCE_SQL = """CASE WHEN o.channel IN ('amazon', 'goodwillbooks') THEN 'cashmonkey'
                     WHEN o.channel = 'ebay' AND o.line_of_business = 'books' THEN 'cashmonkey'
                     ELSE 'upright' END"""
HUB_SOURCES = {
    "upright": {"label": "Upright", "kind": "ecommerce", "channels": ["shopgoodwill", "ebay", "goodwillfinds"]},
    "cashmonkey": {"label": "Cash Monkey", "kind": "ecommerce", "channels": ["amazon", "ebay", "goodwillbooks"]},
    "supro": {"label": "Supro", "kind": "stores", "channels": ["stores"]},
    "thriftly": {"label": "Thriftly", "kind": "production", "channels": []},
}
REPORT_SOURCE = {"upright_paid_orders": "upright", "cashmonkey_orders": "cashmonkey"}
THRIFTLY_DEFINITIONS = {
    "pieces": "Items processed: donated items identified (sorted and priced for e-commerce) in the back room that day.",
    "labor_hours": "Hours worked that day by the production team (sum of timesheet hours).",
    "sent_to_ecom": "Items manifested (shipped from a store to the e-commerce center) that day.",
    "listed": "Items listed online for the first time that day (relists not counted).",
    "sold": "Items whose first sale happened that day.",
    "dates": "Business dates in America/New_York.",
}


def _f(x) -> float:
    return round(float(x or 0), 2)


def export_hub(out: Path, pulse: dict, quality: dict) -> None:
    from goodwill_pulse.gen.supro import COLS as SUPRO_COLS, DEFINITIONS as SUPRO_DEFS, supro_days

    con = duckdb.connect(str(DATA_DIR / "harmonized.duckdb"), read_only=True)
    first, last = con.execute("SELECT min(business_date), max(business_date) FROM fct_orders").fetchone()
    stores = dict(con.execute("SELECT store_id, store_name FROM dim_store ORDER BY 1").fetchall())

    # hub/meta ---------------------------------------------------------------------------------------------------
    fresh = {}
    if pulse:
        day = max(pulse)
        for s in pulse[day].get("sources") or []:
            src = REPORT_SOURCE.get(s.get("report_type"))
            if src:
                fresh[src] = {"status": s.get("status"), "latest_order_at": s.get("latest_order_at"),
                              "label": s.get("label"), "covered_through": s.get("covered_through"),
                              "business_date": day, "files": s.get("files") or []}
    failed = [c for c in (quality.get("checks") or []) if c.get("status") == "fail"]
    checks = {"blocking": int(quality.get("blocking") or 0),
              "errors": sum(1 for c in failed if c.get("severity") == "error"),
              "warnings": sum(1 for c in failed if c.get("severity") == "warning"),
              "total": quality.get("total"), "passed": quality.get("passed"), "run_at": quality.get("run_at")}
    put_doc(out, "hub", "meta", {"first": str(first), "last": str(last), "timezone": TZ, "stores": stores,
                                 "sources": HUB_SOURCES, "freshness": fresh, "checks": checks})

    # hub/days ---------------------------------------------------------------------------------------------------
    rows = con.execute(f"""
        WITH u AS (SELECT order_key, sum(quantity) units FROM fct_order_lines GROUP BY 1)
        SELECT o.business_date, {SOURCE_SQL} src, o.channel, count(*), coalesce(sum(u.units), 0),
               sum(o.subtotal), sum(coalesce(o.shipping_charged, 0)), sum(coalesce(o.marketplace_fees, 0)),
               sum(coalesce(o.refund_amount, 0))
        FROM fct_orders o LEFT JOIN u USING (order_key) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
    put_doc(out, "hub", "days", {"cols": ["date", "source", "channel", "orders", "units", "item_sales", "shipping", "fees", "refunds"],
                                 "rows": [[str(d), s, ch, int(n), int(u), _f(a), _f(sh), _f(fe), _f(r)]
                                          for d, s, ch, n, u, a, sh, fe, r in rows]})

    # storeday/<YYYY-MM> -----------------------------------------------------------------------------------------
    # Shipping is charged per order; each line gets a share in proportion to its sale price (even split if the order's
    # item total is zero). Orders = distinct orders with at least one item from that store.
    rows = con.execute(f"""
        WITH l AS (
          SELECT o.business_date d, {SOURCE_SQL} src, o.channel ch, coalesce(l.store_id, '') store, o.order_key,
                 l.quantity q, l.sale_price sp,
                 coalesce(o.shipping_charged, 0) * CASE WHEN sum(l.sale_price) OVER w <> 0 THEN l.sale_price / sum(l.sale_price) OVER w
                                                        ELSE 1.0 / count(*) OVER w END ship
          FROM fct_order_lines l JOIN fct_orders o USING (order_key)
          WINDOW w AS (PARTITION BY o.order_key))
        SELECT d, src, ch, store, count(DISTINCT order_key), sum(q), sum(sp), sum(ship)
        FROM l GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4""").fetchall()
    by_month = {}
    for d, s, ch, st, n, q, sp, sh in rows:
        by_month.setdefault(d.strftime("%Y-%m"), []).append([str(d), s, ch, st, int(n), int(q or 0), _f(sp), _f(sh)])
    for m, rs in sorted(by_month.items()):
        put_doc(out, "storeday", m, {"month": m, "cols": ["date", "source", "channel", "store", "orders", "units", "item_sales", "shipping"],
                                     "rows": rs})

    # supro/<YYYY-MM> --------------------------------------------------------------------------------------------
    srows, sinfo = supro_days(con, first, last)
    smonths = {}
    for r in srows:
        smonths.setdefault(r[0][:7], []).append(r)
    for m, rs in sorted(smonths.items()):
        put_doc(out, "supro", m, {"month": m, "cols": SUPRO_COLS, "rows": rs, **sinfo[m], "definitions": SUPRO_DEFS})

    # thriftly/all -----------------------------------------------------------------------------------------------
    rows = con.execute(f"""
        WITH ev AS (
          SELECT CAST(timezone('{TZ}', identified_at_utc) AS DATE) d, 1 pc, 0 se, 0 li, 0 so FROM fct_items WHERE identified_at_utc IS NOT NULL
          UNION ALL SELECT CAST(timezone('{TZ}', manifested_at_utc) AS DATE), 0, 1, 0, 0 FROM fct_items WHERE manifested_at_utc IS NOT NULL
          UNION ALL SELECT CAST(timezone('{TZ}', first_listed_at_utc) AS DATE), 0, 0, 1, 0 FROM fct_items WHERE first_listed_at_utc IS NOT NULL
          UNION ALL SELECT CAST(timezone('{TZ}', first_sold_at_utc) AS DATE), 0, 0, 0, 1 FROM fct_items WHERE first_sold_at_utc IS NOT NULL),
        e AS (SELECT d, sum(pc) pc, sum(se) se, sum(li) li, sum(so) so FROM ev GROUP BY 1),
        lab AS (SELECT work_date d, sum(hours) h FROM fct_labor GROUP BY 1),
        cal AS (SELECT CAST(x AS DATE) d FROM range(DATE '{first}', DATE '{last}' + INTERVAL 1 DAY, INTERVAL 1 DAY) t(x))
        SELECT cal.d, coalesce(e.pc, 0), coalesce(lab.h, 0), coalesce(e.se, 0), coalesce(e.li, 0), coalesce(e.so, 0)
        FROM cal LEFT JOIN e USING (d) LEFT JOIN lab USING (d) ORDER BY 1""").fetchall()
    put_doc(out, "thriftly", "all", {"cols": ["date", "pieces", "labor_hours", "sent_to_ecom", "listed", "sold"],
                                     "rows": [[str(d), int(p), _f(h), int(se), int(li), int(so)] for d, p, h, se, li, so in rows],
                                     "definitions": THRIFTLY_DEFINITIONS})
    con.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), "--hub-only" in sys.argv[2:])
