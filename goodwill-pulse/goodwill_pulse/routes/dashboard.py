"""/api/dashboard: the Monthly E-Commerce Dashboard (Feature 2). Owned by Engineer 7, see docs/CONTRACT.md.

Every number comes from `goodwill_pulse.kpi` (Engineer 6) except the two breakdown tables (stores, categories),
which are plain grouped sums over `data/harmonized.duckdb`. The DB is opened read_only per request.
If the harmonized DB or the KPI module is not there yet, the endpoints answer 503 with a clear message.
"""
from __future__ import annotations

import importlib
import math
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import duckdb
from fastapi import APIRouter, HTTPException, Query

from ..config import DATA_DIR

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

HARMONIZED_PATH: Path = DATA_DIR / "harmonized.duckdb"   # tests monkeypatch this
KPI_MODULE = "goodwill_pulse.kpi"                         # tests may monkeypatch this to a fake module
TZ = "America/New_York"

AVAILABILITY = {
    "built": {"label": "Source report", "detail": "Computed from marketplace and operations data."},
    "input": {"label": "Manual input", "detail": "Uses a monthly figure entered by Finance (overhead, store retail)."},
    "n/a": {"label": "Not yet available", "detail": "No data source yet; shown so the gap is visible."},
}
PILLARS = ["growth", "profitability", "productivity", "inventory", "engagement"]

# Slide 35 lays the 15 KPIs out in five rows. kpi.py has no row field, so we derive it from the KPI id
# (first matching keyword wins), falling back to the pillar. If a card carries "row" or "group", that wins.
SCORECARD_ROWS = ["Financial", "Productivity", "Inventory", "Sales", "Category + Customer"]
_ROW_KEYWORDS = [
    ("Category + Customer", ("categor", "top10", "top_10", "repeat", "buyer", "customer")),
    ("Productivity", ("per_labor", "labor_hour", "per_employee", "listings_created", "listings", "employee")),
    ("Inventory", ("sell_through", "backlog", "unsold", "days", "relist", "inventory")),
    ("Sales", ("asp", "average_selling", "avg_selling", "selling_price", "sale_price", "median", "units")),
    ("Financial", ("revenue", "growth", "margin", "budget", "share")),
]
_PILLAR_ROW = {"growth": "Financial", "profitability": "Financial", "productivity": "Productivity",
               "inventory": "Inventory", "engagement": "Category + Customer"}


# ------------------------------------------------------------------ helpers
def _kpi():
    """Import the KPI module lazily so this router loads even before kpi.py exists."""
    try:
        mod = importlib.import_module(KPI_MODULE)
    except Exception as e:  # ImportError, or a SyntaxError while kpi.py is half-written
        raise HTTPException(503, f"KPI module not available yet ({KPI_MODULE}: {type(e).__name__}: {e}).")
    missing = [n for n in ("KPIS", "compute", "kpi_card", "scorecard", "series") if not hasattr(mod, n)]
    if missing:
        raise HTTPException(503, f"KPI module is incomplete; missing {', '.join(missing)}.")
    return mod


@contextmanager
def _con() -> Iterator[duckdb.DuckDBPyConnection]:
    path = Path(HARMONIZED_PATH)
    if not path.exists():
        raise HTTPException(503, f"Harmonized data not built yet ({path.name} missing). "
                                 "Run: .venv/bin/python -m goodwill_pulse.build")
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as e:
        raise HTTPException(503, f"Harmonized data is being rebuilt, try again in a moment ({e}).")
    try:
        yield con
    finally:
        con.close()


def _month(s: str | None) -> date | None:
    if not s:
        return None
    try:
        y, m = s.split("-")[:2]
        return date(int(y), int(m), 1)
    except Exception:
        raise HTTPException(422, f"month must look like YYYY-MM, got {s!r}")


def _shift(m: date, n: int) -> date:
    k = m.year * 12 + (m.month - 1) + n
    return date(k // 12, k % 12 + 1, 1)


def _clean(v: Any) -> Any:
    """JSON-safe: Decimal → float, dates → ISO, NaN/inf → None (missing data is null, never 0)."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if hasattr(v, "item") and callable(v.item):   # numpy scalars
        return _clean(v.item())
    return v


def _rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _months_available(con) -> list[date]:
    rows = con.execute("SELECT DISTINCT date_trunc('month', business_date)::DATE AS m FROM fct_orders "
                       "ORDER BY m DESC").fetchall()
    return [r[0] for r in rows]


def _default_month(con, months: list[date]) -> date | None:
    """Latest complete month: if the newest month is still in progress, step back one."""
    if not months:
        return None
    last_day = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    newest = months[0]
    month_end = _shift(newest, 1)
    if last_day is not None and (month_end - last_day).days > 1 and len(months) > 1:
        return months[1]
    return newest


def _resolve_filters(con, month: str | None, store: str | None, channel: str | None):
    m = _month(month)
    if m is None:
        m = _default_month(con, _months_available(con))
        if m is None:
            raise HTTPException(404, "No orders in the harmonized data yet.")
    store = store or None
    channel = channel or None
    if store and not con.execute("SELECT 1 FROM dim_store WHERE store_id = ?", [store]).fetchone():
        raise HTTPException(422, f"Unknown store {store!r}.")
    if channel and not con.execute("SELECT 1 FROM dim_channel WHERE channel = ?", [channel]).fetchone():
        raise HTTPException(422, f"Unknown channel {channel!r}.")
    return m, store, channel


def _row_for(card: dict) -> str:
    for key in ("scorecard_row", "row", "group"):
        if card.get(key) in SCORECARD_ROWS:
            return card[key]
    kid = str(card.get("id", "")).lower()
    for row, words in _ROW_KEYWORDS:
        if any(w in kid for w in words):
            return row
    return _PILLAR_ROW.get(card.get("pillar"), "Financial")


def _missing(cards: list[dict]) -> list[str]:
    """KPIs that should have a value (built from data) but came back null."""
    return [c.get("id") for c in cards if c.get("availability") == "built" and c.get("value") is None]


# ------------------------------------------------------------------ endpoints
@router.get("/options")
def options() -> dict:
    """Filter choices. Does not need the KPI module."""
    with _con() as con:
        months = _months_available(con)
        default = _default_month(con, months)
        stores = _rows(con, "SELECT store_id, store_name, city, ai_flagging, ecom_eye FROM dim_store ORDER BY store_id")
        channels = _rows(con, "SELECT channel, label, listing_tool FROM dim_channel ORDER BY channel")
        last_day = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    kpis, kpi_ready, kpi_message = [], True, None
    try:
        mod = _kpi()
        kpis = [{"id": k, "label": d.label, "pillar": d.pillar, "unit": d.unit, "better": d.better,
                 "availability": d.availability} for k, d in mod.KPIS.items()]
    except HTTPException as e:
        kpi_ready, kpi_message = False, e.detail
    return _clean({"months": [m.strftime("%Y-%m") for m in months],
                   "default_month": default.strftime("%Y-%m") if default else None,
                   "data_through": last_day, "stores": stores, "channels": channels,
                   "pillars": PILLARS, "availability": AVAILABILITY,
                   "kpis": kpis, "kpi_ready": kpi_ready, "kpi_message": kpi_message})


@router.get("/kpis")
def kpis(month: str | None = None, store: str | None = None, channel: str | None = None) -> dict:
    mod = _kpi()
    with _con() as con:
        m, store, channel = _resolve_filters(con, month, store, channel)
        sc = _clean(mod.scorecard(con, m, store=store, channel=channel))
    anchors = sc.get("anchors") or []
    cards = sc.get("scorecard") or []
    pillars = sc.get("pillars") or {}
    for c in cards:
        c["row"] = _row_for(c)
    rows = [{"label": r, "ids": [c["id"] for c in cards if c["row"] == r]} for r in SCORECARD_ROWS]
    all_cards = {c.get("id"): c for c in [*anchors, *cards, *(x for p in pillars.values() for x in p)]}
    return {"month": m.strftime("%Y-%m"), "filters": {"store": store, "channel": channel},
            "anchors": anchors, "scorecard": cards, "scorecard_rows": rows,
            "pillars": {p: pillars.get(p, []) for p in PILLARS} | {p: v for p, v in pillars.items() if p not in PILLARS},
            "availability": AVAILABILITY, "missing_values": _missing(list(all_cards.values()))}


@router.get("/series")
def series(kpi: str, months: int = Query(13, ge=1, le=36), month: str | None = None,
           store: str | None = None, channel: str | None = None,
           by: str | None = Query(None, pattern="^(channel)$")) -> dict:
    """A KPI's monthly trend. Ends at `month` if given, else wherever kpi.series ends (latest data).
    `by=channel` returns one series per marketplace (ignores the channel filter)."""
    mod = _kpi()
    if kpi not in mod.KPIS:
        raise HTTPException(404, f"Unknown KPI {kpi!r}.")
    d = mod.KPIS[kpi]

    def one(con, ch):
        if month:
            end = _month(month)
            try:   # kpi.series(..., end=) computes the whole range in one pass
                pts = mod.series(con, kpi, months, store=store, channel=ch, end=end)
                return [{**p, "month": str(p.get("month"))[:7]} for p in _clean(pts)]
            except TypeError:
                pass
            out = []
            for i in range(months - 1, -1, -1):
                mm = _shift(end, -i)
                out.append({"month": mm.strftime("%Y-%m"),
                            "value": mod.compute(con, kpi, mm, store=store, channel=ch)})
            return _clean(out)
        return [{**p, "month": str(p.get("month"))[:7]} for p in _clean(mod.series(con, kpi, months, store=store, channel=ch))]

    with _con() as con:
        _, store, channel = _resolve_filters(con, month, store, channel)
        meta = {"kpi": kpi, "label": d.label, "unit": d.unit, "pillar": d.pillar, "better": d.better,
                "filters": {"store": store, "channel": None if by else channel, "month": month, "months": months}}
        if by == "channel":
            chans = _rows(con, "SELECT channel, label FROM dim_channel ORDER BY channel")
            return meta | {"by": "channel",
                           "series": [{"key": c["channel"], "label": c["label"], "points": one(con, c["channel"])}
                                      for c in chans]}
        return meta | {"points": one(con, channel)}


def _month_bounds(m: date) -> tuple[date, date]:
    return m, _shift(m, 1)


@router.get("/stores")
def stores(month: str | None = None, channel: str | None = None) -> dict:
    """Revenue credited to the originating store (the store that sent the item gets the sale), plus the
    item pipeline it fed: identified → sent (manifested) → listed, all in the month (ET).
    Rows with no store are kept as 'Unattributed' (a data-quality exception, never dropped)."""
    with _con() as con:
        m, _, channel = _resolve_filters(con, month, None, channel)
        lo, hi = _month_bounds(m)
        ch_sql, ch_p = ("AND channel = ?", [channel]) if channel else ("", [])
        rows = _rows(con, f"""
            WITH sales AS (
                SELECT store_id, sum(sale_price) AS revenue, sum(quantity) AS units, count(DISTINCT order_key) AS orders
                FROM fct_order_lines WHERE business_date >= ? AND business_date < ? {ch_sql}
                GROUP BY store_id),
            pipe AS (
                SELECT store_id,
                  count(*) FILTER (WHERE timezone('{TZ}', identified_at_utc)::DATE >= ? AND timezone('{TZ}', identified_at_utc)::DATE < ?) AS items_identified,
                  count(*) FILTER (WHERE timezone('{TZ}', manifested_at_utc)::DATE >= ? AND timezone('{TZ}', manifested_at_utc)::DATE < ?) AS items_sent,
                  count(*) FILTER (WHERE timezone('{TZ}', first_listed_at_utc)::DATE >= ? AND timezone('{TZ}', first_listed_at_utc)::DATE < ?) AS items_listed,
                  count(*) FILTER (WHERE manifested_at_utc < timezone('{TZ}', ?::TIMESTAMP)
                                   AND (first_listed_at_utc IS NULL OR first_listed_at_utc >= timezone('{TZ}', ?::TIMESTAMP))) AS backlog_end
                FROM fct_items GROUP BY store_id),
            keys AS (SELECT store_id FROM dim_store UNION SELECT store_id FROM sales UNION SELECT store_id FROM pipe)
            SELECT k.store_id, coalesce(d.store_name, CASE WHEN k.store_id IS NULL THEN 'Unattributed' ELSE k.store_id END) AS store_name,
                   d.city, d.ai_flagging, d.ecom_eye,
                   coalesce(s.revenue, 0) AS revenue, coalesce(s.units, 0) AS units, coalesce(s.orders, 0) AS orders,
                   coalesce(p.items_identified, 0) AS items_identified, coalesce(p.items_sent, 0) AS items_sent,
                   coalesce(p.items_listed, 0) AS items_listed, coalesce(p.backlog_end, 0) AS backlog_end
            FROM keys k LEFT JOIN dim_store d ON d.store_id = k.store_id
              LEFT JOIN sales s ON s.store_id IS NOT DISTINCT FROM k.store_id
              LEFT JOIN pipe p ON p.store_id IS NOT DISTINCT FROM k.store_id
            ORDER BY revenue DESC, k.store_id NULLS LAST""",
                     [lo, hi, *ch_p, lo, hi, lo, hi, lo, hi, hi, hi])
    rows = [r for r in rows if r["store_id"] is not None or r["revenue"] or r["items_sent"] or r["items_identified"]]
    total = sum(float(r["revenue"]) for r in rows)
    for r in rows:
        r["asp"] = float(r["revenue"]) / r["units"] if r["units"] else None
        r["share"] = float(r["revenue"]) / total if total else None
    return _clean({"month": m.strftime("%Y-%m"), "filters": {"channel": channel},
                   "basis": "Item sale price credited to the store that sent the item (fct_order_lines.store_id); "
                            "pipeline counts from fct_items by ET date.",
                   "total_revenue": total, "stores": rows})


@router.get("/categories")
def categories(month: str | None = None, by: str = Query("revenue", pattern="^(revenue|margin)$"),
               store: str | None = None, channel: str | None = None, limit: int = Query(10, ge=1, le=50)) -> dict:
    """Top categories for the month by revenue or by gross profit $ (after fees and refunds)."""
    with _con() as con:
        m, store, channel = _resolve_filters(con, month, store, channel)
        lo, hi = _month_bounds(m)
        where, p = ["l.business_date >= ?", "l.business_date < ?"], [lo, hi]
        if store:
            where.append("l.store_id = ?"); p.append(store)
        if channel:
            where.append("l.channel = ?"); p.append(channel)
        order = "revenue" if by == "revenue" else "gross_profit"
        rows = _rows(con, f"""
            WITH lines AS (
                SELECT coalesce(l.category, 'Uncategorized') AS category, l.sale_price, l.quantity,
                       coalesce(l.fee_alloc, 0) AS fee,
                       CASE WHEN o.subtotal > 0 THEN coalesce(o.refund_amount, 0) * l.sale_price / o.subtotal ELSE 0 END AS refund
                FROM fct_order_lines l LEFT JOIN fct_orders o USING (order_key)
                WHERE {' AND '.join(where)})
            SELECT category, sum(sale_price) AS revenue, sum(quantity) AS units,
                   sum(fee) AS fees, sum(refund) AS refunds, sum(sale_price - fee - refund) AS gross_profit
            FROM lines GROUP BY category ORDER BY {order} DESC, category""", p)
    total = sum(float(r["revenue"]) for r in rows)
    out = []
    for i, r in enumerate(rows[:limit]):
        rev = float(r["revenue"])
        out.append(r | {"rank": i + 1, "asp": rev / r["units"] if r["units"] else None,
                        "margin_pct": float(r["gross_profit"]) / rev if rev else None,
                        "share": rev / total if total else None})
    return _clean({"month": m.strftime("%Y-%m"), "by": by, "filters": {"store": store, "channel": channel},
                   "basis": "Gross profit = sale price − allocated marketplace fees − refunds allocated pro rata. "
                            "Donated goods have no purchase cost. Outbound label cost is not in the harmonized model yet.",
                   "total_revenue": total, "categories": out, "category_count": len(rows)})
