"""Run the trust engine on Odin's data.

    from goodwill_pulse.trust import odin, compute
    con = odin.connect()                      # data/harmonized.duckdb, read-only, with sales_line + books views
    compute(con, "total_sales", breakdown="channel", period="yesterday")

    python -m goodwill_pulse.trust --odin     # planted answers from Odin's demo files + kpi.py cross-check

Three layers of agreement on the same data:
1. Planted answers: Odin's own answer key for the Friday Eastern-time Upright export (demo_data/.../expected.json),
   which was generated from report files, not from harmonized.duckdb.
2. The trust engine's own checks on every answer (recount, parts = whole, refunds netted...).
3. Cross-check: Odin's kpi.py (written separately) must get the same monthly numbers per platform.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

from ..config import DATA_DIR, ROOT
from .engine import compute

HARMONIZED = DATA_DIR / "harmonized.duckdb"
VIEWS_SQL = ROOT / "sql" / "trust" / "views.sql"
DEMO_DATA = ROOT / "demo_data"

CHANNEL_NAMES = {"shopgoodwill": "ShopGoodwill", "ebay": "eBay", "amazon": "Amazon",
                 "goodwillfinds": "GoodwillFinds", "goodwillbooks": "Goodwillbooks"}

# PLACEHOLDER until Stardess / Amanda confirm: Odin's 10 categories -> Goodwill's report departments.
# A category not listed here gets no department, and the "every line has a department" check flags it.
DEPARTMENTS = {
    "Jewelry": "Jewelry", "Watches": "Jewelry",
    "Clothing": "Textiles", "Shoes": "Textiles",
    "Collectibles": "Wares/Hard Goods", "Home Decor": "Wares/Hard Goods", "Electronics": "Wares/Hard Goods",
    "Toys & Games": "Wares/Hard Goods", "Art": "Wares/Hard Goods",
    "Books": "Books",
}
GENERAL_MERCH = ["Jewelry", "Textiles", "Wares/Hard Goods"]   # what Upright sells (books go through Cash Monkey)


def _case(col: str, mapping: dict, default: str) -> str:
    whens = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in mapping.items())
    return f"CASE {col} {whens} ELSE {default} END"


def connect(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open Odin's harmonized DB read-only and add the trust engine's views (temporary; nothing is written)."""
    con = duckdb.connect(str(path or HARMONIZED), read_only=True)
    sql = VIEWS_SQL.read_text().format(channel_name=_case("channel", CHANNEL_NAMES, "channel"),
                                       department=_case("category", DEPARTMENTS, "NULL"))
    con.execute(sql)
    return con


def answers_from_demo_data(demo_dir: Path = DEMO_DATA) -> list[dict]:
    """Planted answers from Odin's answer key for the Friday Upright export in Eastern time (the same day boundary
    as harmonized.duckdb). Upright carries general merchandise only, so answers filter to those departments."""
    folder = demo_dir / "02_friday_2026-10-02"
    exp = json.loads((folder / "expected.json").read_text())
    day = exp["business_day_pacific"]   # same calendar day; the eastern_time_export block uses ET boundaries
    period = {"start": day, "end": day}
    src = f"{folder.name}/expected.json (eastern_time_export)"
    out = []
    for raw_name, e in exp["eastern_time_export"]["by_channel"].items():
        channel = CHANNEL_NAMES.get(raw_name.lower(), raw_name)
        for key, metric in (("orders", "orders"), ("item_sales", "gross_sales"),
                            ("shipping_total", "shipping_charged"), ("units", "items_sold")):
            out.append({"question": f"{channel} {key.replace('_', ' ')} on {day} (Odin's answer key)",
                        "metric": metric, "filters": {"channel": channel, "department": GENERAL_MERCH},
                        "period": period, "expect": e[key], "source": src})
    return out


# trust metric <-> Odin kpi.py id
_PAIRS = [("gross_sales", "total_revenue"), ("customers", "buyers")]


def cross_check(con: duckdb.DuckDBPyConnection, months: int = 14) -> list[tuple]:
    """[(section, name, ok, detail)]: Odin's kpi.py vs the trust engine, every month, total and per platform."""
    from .. import kpi
    section = "Odin's kpi.py vs trust engine (monthly)"
    channels = [None] + [r[0] for r in con.execute("SELECT DISTINCT channel FROM fct_orders ORDER BY 1").fetchall()]
    lines = []
    for ch in channels:
        filters = {"channel": CHANNEL_NAMES.get(ch, ch)} if ch else None
        label = CHANNEL_NAMES.get(ch, ch) if ch else "All platforms"
        mine = {}
        for metric in ("gross_sales", "customers", "items_sold"):
            res = compute(con, metric, filters, breakdown="month", period="all")
            mine[metric] = {r["key"]: r["value"] for r in res["rows"]}
        mine["avg_selling_price"] = {m: round(v / mine["items_sold"][m], 2)
                                     for m, v in mine["gross_sales"].items() if mine["items_sold"].get(m)}
        for metric, kid in _PAIRS + [("avg_selling_price", "avg_selling_price")]:
            theirs = {p["month"]: p["value"] for p in kpi.series(con, kid, months=months, channel=ch)
                      if p["value"] is not None}
            bad = [(m, mine[metric].get(m), v) for m, v in theirs.items()
                   if mine[metric].get(m) is None or abs(mine[metric][m] - v) > 0.011]
            detail = (f"{len(theirs)} months match" if not bad else
                      f"{len(bad)}/{len(theirs)} months differ, e.g. {bad[0][0]}: ours {bad[0][1]} vs kpi {bad[0][2]}")
            lines.append((section, f"{label}: {metric} = kpi.{kid}", not bad and bool(theirs), detail))
    return lines

