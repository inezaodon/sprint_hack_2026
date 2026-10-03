"""Export the app's views as JSON documents for the published artifact's database.

    goodwill-pulse/.venv/bin/python tools/export_artifact_docs.py <out_dir>

Writes <collection>__<doc_id>.json files (each under the 256 KiB document limit), ready to load with ArtifactData
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


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    c = TestClient(app)

    def get(path):
        r = c.get(path)
        if r.status_code != 200:
            raise SystemExit(f"{path}: {r.status_code} {r.text[:200]}")
        return r.json()

    def put(coll, doc_id, obj):
        s = json.dumps(obj, default=str, separators=(",", ":"))
        assert len(s) < 250_000, (coll, doc_id, len(s))
        (out / f"{coll}__{doc_id}.json").write_text(s)
        print(f"{coll}/{doc_id}: {len(s):,} bytes")

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
    put("pulse", "all", {d: get(f"/api/pulse?d={d}") for d in get("/api/pulse/dates")[:21]})
    for m in with_data:
        put("close", m["month"], get(f"/api/close/run?month={m['month']}"))
    put("quality", "latest", get("/api/close/quality"))

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


if __name__ == "__main__":
    main(Path(sys.argv[1]))
