"""Ask the data (PLAN C4): questions answered ONLY through a fixed set of tools (never free-form SQL).

    ask("Which stores sent the most items to e-commerce last month?") ->
        {"answer", "engine": claude|fallback, "citations": [{tool, filters}], "results": [...], "checked_numbers"}

Tools wrap Engineer 6's goodwill_pulse.kpi (kpi_value, kpi_trend, kpi_breakdown) plus a few parameterized
queries on harmonized.duckdb (revenue_by, top_categories, items_by_store, open_dq_failures). If kpi.py can't be
imported, the KPI tools are simply not offered. Claude picks tools when a key exists; otherwise a keyword router
maps common questions to the same tools. Every answer is number-checked against the tool results.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import duckdb

from ..config import DATA_DIR
from . import client as ai
from .narrator import PRESCRIPTIVE_RE, _jsonable, money
from .numbers import check_numbers

try:   # Engineer 6's module; degrade gracefully if it is missing or broken
    from .. import kpi as _kpi
except Exception:  # noqa: BLE001
    _kpi = None

HARMONIZED_PATH = DATA_DIR / "harmonized.duckdb"
CHANNELS = ["amazon", "ebay", "goodwillbooks", "goodwillfinds", "shopgoodwill"]
CHANNEL_LABEL = {"shopgoodwill": "ShopGoodwill", "ebay": "eBay", "amazon": "Amazon", "goodwillfinds": "GoodwillFinds",
                 "goodwillbooks": "Goodwillbooks"}
STAGE_COL = {"identified": "identified_at_utc", "sent": "manifested_at_utc", "listed": "first_listed_at_utc",
             "sold": "first_sold_at_utc"}
TZ = "America/New_York"


class ToolError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# argument helpers
def _month(v: Any) -> date:
    if isinstance(v, date):
        return v.replace(day=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", str(v or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ToolError(f"month must look like YYYY-MM, got {v!r}")
    return date(int(m.group(1)), int(m.group(2)), 1)


def _next_month(m: date) -> date:
    return date(m.year + m.month // 12, m.month % 12 + 1, 1)


def _channel(v: Any) -> str | None:
    if v in (None, "", "all"):
        return None
    if v not in CHANNELS:
        raise ToolError(f"channel must be one of {CHANNELS}, got {v!r}")
    return v


def _store(v: Any) -> str | None:
    if v in (None, "", "all"):
        return None
    m = re.fullmatch(r"(?i)store\s*0?(\d{1,2})", str(v).strip())
    if not m:
        raise ToolError(f"store must look like Store07, got {v!r}")
    return f"Store{int(m.group(1)):02d}"


def _limit(v: Any, default: int = 10) -> int:
    try:
        return max(1, min(int(v if v is not None else default), 25))
    except (TypeError, ValueError):
        return default


def _rows(con, sql: str, params: list) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [_jsonable(dict(zip(cols, r))) for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------------------------
# tools: each returns JSON-able data; the executor wraps it with {tool, filters}
def t_revenue_by(con, month, by, channel=None, store=None, limit=None) -> dict:
    m, ch, st, n = _month(month), _channel(channel), _store(store), _limit(limit, 25)
    if by not in ("store", "channel", "category"):
        raise ToolError("by must be store, channel or category")
    if by == "channel" and not st:
        where, params = ["business_date >= ? AND business_date < ?"], [m, _next_month(m)]
        if ch:
            where.append("channel = ?"); params.append(ch)
        rows = _rows(con, f"""SELECT channel AS key, round(sum(subtotal), 2)::DOUBLE AS revenue, count(*) AS orders
                              FROM fct_orders WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC LIMIT ?""",
                     params + [n])
        basis = "fct_orders.subtotal (Daily Pulse revenue basis)"
    else:
        key = {"store": "coalesce(store_id, '(no store)')", "channel": "channel", "category": "coalesce(category, '(no category)')"}[by]
        where, params = ["business_date >= ? AND business_date < ?"], [m, _next_month(m)]
        if ch:
            where.append("channel = ?"); params.append(ch)
        if st:
            where.append("store_id = ?"); params.append(st)
        rows = _rows(con, f"""SELECT {key} AS key, round(sum(sale_price), 2)::DOUBLE AS revenue,
                                     sum(quantity)::BIGINT AS units, count(DISTINCT order_key) AS orders
                              FROM fct_order_lines WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC LIMIT ?""",
                     params + [n])
        basis = "fct_order_lines.sale_price (credited to originating store)"
    total = round(sum(r["revenue"] or 0 for r in rows), 2)
    return {"rows": rows, "total_revenue_of_rows": total, "basis": basis}


def t_top_categories(con, month, metric="revenue", channel=None, store=None, limit=10) -> dict:
    m, ch, st, n = _month(month), _channel(channel), _store(store), _limit(limit)
    if metric not in ("revenue", "units"):
        raise ToolError("metric must be revenue or units")
    where, params = ["business_date >= ? AND business_date < ?"], [m, _next_month(m)]
    if ch:
        where.append("channel = ?"); params.append(ch)
    if st:
        where.append("store_id = ?"); params.append(st)
    order = "revenue" if metric == "revenue" else "units"
    rows = _rows(con, f"""SELECT coalesce(category, '(no category)') AS key, round(sum(sale_price), 2)::DOUBLE AS revenue,
                                 sum(quantity)::BIGINT AS units
                          FROM fct_order_lines WHERE {' AND '.join(where)}
                          GROUP BY 1 ORDER BY {order} DESC, 1 LIMIT ?""", params + [n])
    return {"rows": rows, "ranked_by": metric}


def t_items_by_store(con, month, stage="sent", limit=10) -> dict:
    m, n = _month(month), _limit(limit)
    if stage not in STAGE_COL:
        raise ToolError(f"stage must be one of {list(STAGE_COL)}")
    col = STAGE_COL[stage]
    rows = _rows(con, f"""SELECT coalesce(store_id, '(no store)') AS key, count(*) AS items
                          FROM fct_items
                          WHERE date_trunc('month', timezone('{TZ}', {col}))::DATE = ?
                          GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?""", [m, n])
    total = con.execute(f"""SELECT count(*) FROM fct_items
                            WHERE date_trunc('month', timezone('{TZ}', {col}))::DATE = ?""", [m]).fetchone()[0]
    return {"rows": rows, "stage": stage, "all_stores_total": total}


def t_open_dq_failures(con) -> dict:
    try:
        rows = _rows(con, """
            WITH latest AS (SELECT check_id, max(run_at) AS run_at FROM dq_results GROUP BY 1)
            SELECT d.check_id, d.severity, d.failing_rows, d.description, d.run_at
            FROM dq_results d JOIN latest USING (check_id, run_at)
            WHERE d.status = 'fail' ORDER BY CASE d.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     d.failing_rows DESC""", [])
        checks_run = con.execute("SELECT count(DISTINCT check_id) FROM dq_results").fetchone()[0]
    except duckdb.Error as e:
        raise ToolError(f"dq_results not available: {e}") from e
    return {"failing_checks": rows, "checks_run": checks_run}


def t_kpi_value(con, kpi_id, month, store=None, channel=None) -> dict:
    _need_kpi(kpi_id)
    card = _kpi.kpi_card(con, kpi_id, _month(month), _store(store), _channel(channel))
    keep = ("id", "label", "unit", "value", "prior_month", "prior_year", "status", "provisional", "notes",
            "formula", "as_of")
    out = {k: card.get(k) for k in keep}
    if card.get("breakdown"):
        out["breakdown"] = card["breakdown"][:10]
    return _jsonable(out)


def t_kpi_trend(con, kpi_id, months=13, end_month=None, store=None, channel=None) -> dict:
    _need_kpi(kpi_id)
    n = max(2, min(int(months or 13), 25))
    pts = _kpi.series(con, kpi_id, n, _store(store), _channel(channel), end=_month(end_month) if end_month else None)
    kd = _kpi.KPIS[kpi_id]
    return _jsonable({"id": kpi_id, "label": kd.label, "unit": kd.unit, "points": pts})


def t_kpi_breakdown(con, kpi_id, month, by, top=10, store=None, channel=None) -> dict:
    _need_kpi(kpi_id)
    if by not in ("store", "channel", "category"):
        raise ToolError("by must be store, channel or category")
    rows = _kpi.breakdown(con, kpi_id, _month(month), by, _store(store), _channel(channel), top=_limit(top))
    kd = _kpi.KPIS[kpi_id]
    return _jsonable({"id": kpi_id, "label": kd.label, "unit": kd.unit, "by": by, "rows": rows})


def _need_kpi(kpi_id: str) -> None:
    if _kpi is None:
        raise ToolError("KPI module not available")
    if kpi_id not in _kpi.KPIS:
        raise ToolError(f"unknown kpi_id {kpi_id!r}")


# ---------------------------------------------------------------------------------------------------------------
# tool schemas (deterministic order -> stable prompt-cache prefix)
_MONTH = {"type": "string", "description": "Calendar month, YYYY-MM"}
_CH = {"type": "string", "enum": CHANNELS, "description": "Optional marketplace filter"}
_ST = {"type": "string", "description": "Optional originating store filter, e.g. Store07"}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def tool_specs() -> list[dict]:
    specs = [
        {"name": "revenue_by", "description": "Revenue for one month grouped by store, channel or category "
         "(store revenue = sales credited to the originating store). Optional channel/store filters.",
         "input_schema": _schema({"month": _MONTH, "by": {"type": "string", "enum": ["store", "channel", "category"]},
                                  "channel": _CH, "store": _ST, "limit": {"type": "integer"}}, ["month", "by"])},
        {"name": "top_categories", "description": "Top product categories for one month ranked by revenue or units.",
         "input_schema": _schema({"month": _MONTH, "metric": {"type": "string", "enum": ["revenue", "units"]},
                                  "channel": _CH, "store": _ST, "limit": {"type": "integer"}}, ["month", "metric"])},
        {"name": "items_by_store", "description": "Items per originating store for one month at a lifecycle stage: "
         "identified (flagged for e-com), sent (manifested to the e-com team), listed (first listing), sold.",
         "input_schema": _schema({"month": _MONTH, "stage": {"type": "string", "enum": list(STAGE_COL)},
                                  "limit": {"type": "integer"}}, ["month", "stage"])},
        {"name": "open_dq_failures", "description": "Data-quality checks failing in the latest check run.",
         "input_schema": _schema({}, [])},
    ]
    if _kpi is not None:
        ids = sorted(_kpi.KPIS)
        catalog = "; ".join(f"{k} = {_kpi.KPIS[k].label} ({_kpi.KPIS[k].unit})" for k in ids)
        kid = {"type": "string", "enum": ids}
        specs += [
            {"name": "kpi_value", "description": "One dashboard KPI for a month with prior month, prior year, "
             "status and formula. pct values are fractions (0.52 = 52%). KPIs: " + catalog,
             "input_schema": _schema({"kpi_id": kid, "month": _MONTH, "store": _ST, "channel": _CH},
                                     ["kpi_id", "month"])},
            {"name": "kpi_trend", "description": "Monthly series of one KPI ending at end_month (default latest).",
             "input_schema": _schema({"kpi_id": kid, "months": {"type": "integer"}, "end_month": _MONTH,
                                      "store": _ST, "channel": _CH}, ["kpi_id"])},
            {"name": "kpi_breakdown", "description": "One KPI for a month split by store, channel or category, "
             "sorted high to low.",
             "input_schema": _schema({"kpi_id": kid, "month": _MONTH,
                                      "by": {"type": "string", "enum": ["store", "channel", "category"]},
                                      "top": {"type": "integer"}, "store": _ST, "channel": _CH},
                                     ["kpi_id", "month", "by"])},
        ]
    return [{**s, "strict": True} for s in specs]


TOOLS: dict[str, Callable] = {"revenue_by": t_revenue_by, "top_categories": t_top_categories,
                              "items_by_store": t_items_by_store, "open_dq_failures": t_open_dq_failures,
                              "kpi_value": t_kpi_value, "kpi_trend": t_kpi_trend, "kpi_breakdown": t_kpi_breakdown}


def run_tool(con, name: str, args: dict) -> dict:
    """Execute one tool call. Returns {tool, filters, data} or {tool, filters, error}."""
    args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    if name not in TOOLS or (name.startswith("kpi_") and _kpi is None):
        return {"tool": name, "filters": args, "error": f"unknown tool {name!r}"}
    try:
        return {"tool": name, "filters": args, "data": TOOLS[name](con, **args)}
    except (ToolError, TypeError) as e:
        return {"tool": name, "filters": args, "error": str(e)}


# ---------------------------------------------------------------------------------------------------------------
# context: what "last month" means is relative to the data, not the wall clock
def data_context(con) -> dict:
    latest = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    if latest is None:
        return {"latest_data_date": None, "latest_month": None, "last_complete_month": None}
    cur = latest.replace(day=1)
    complete = cur if (latest + timedelta(days=1)).month != latest.month else (cur - timedelta(days=1)).replace(day=1)
    return {"latest_data_date": latest.isoformat(), "latest_month": cur.isoformat()[:7],
            "last_complete_month": complete.isoformat()[:7]}


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def parse_month(q: str, ctx: dict) -> str | None:
    ql = q.lower()
    latest = ctx.get("latest_month")
    if not latest:
        return None
    lm = _month(latest)
    if re.search(r"\blast month\b|\bprevious month\b|\bprior month\b", ql):
        return (lm - timedelta(days=1)).replace(day=1).isoformat()[:7]
    if re.search(r"\bthis month\b|\bmonth to date\b|\bmtd\b", ql):
        return latest
    if m := re.search(r"\b(20\d{2})-(\d{2})\b", ql):
        return f"{m.group(1)}-{m.group(2)}"
    for i, name in enumerate(MONTHS, 1):
        if m := re.search(rf"\b{name[:3]}(?:{name[3:]}|t)?\.?\b(?:\s+(20\d{{2}}))?", ql):
            if name == "may" and not m.group(1) and not re.search(r"\bin may\b|\bmay (?:20|\d)", ql):
                continue   # "may" the verb
            year = int(m.group(1)) if m.group(1) else (lm.year if i <= lm.month else lm.year - 1)
            return f"{year}-{i:02d}"
    return None


# ---------------------------------------------------------------------------------------------------------------
# deterministic router (fallback)
_CHANNEL_WORDS = [("shopgoodwill", r"shop ?goodwill|\bsgw\b"), ("goodwillfinds", r"goodwill ?finds"),
                  ("goodwillbooks", r"goodwill ?books"), ("ebay", r"\bebay\b"), ("amazon", r"\bamazon\b")]
_KPI_ALIASES = [   # (regex, kpi_id) most specific first; plain "revenue" goes to revenue_by instead
    (r"sell[- ]?through", "sell_through"), (r"net margin", "net_margin"), (r"gross margin", "gross_margin"),
    (r"revenue per (labor )?hour|rev(enue)? ?/ ?hour", "revenue_per_labor_hour"),
    (r"profit per (labor )?hour", "profit_per_labor_hour"), (r"repeat (buyer|customer)", "repeat_buyer_rate"),
    (r"new (buyer|customer)s?", "new_buyers"), (r"refund rate|refunds?", "refund_rate"),
    (r"backlog|unlisted", "unlisted_backlog"), (r"unsold", "unsold_pct"), (r"relist", "relisted_pct"),
    (r"average selling price|avg selling price|\basp\b|average price", "avg_selling_price"),
    (r"median (sale )?price", "median_sale_price"), (r"days to sell|time to sell", "days_to_sell"),
    (r"donation to listing|days to list", "days_donation_to_listing"), (r"time to list", "avg_time_to_list"),
    (r"listings per employee", "listings_per_employee"), (r"sales per employee", "sales_per_employee"),
    (r"listings (created|posted)|how many listings|listings", "listings_created"),
    (r"(revenue )?growth|year over year|\byoy\b", "revenue_growth_yoy"), (r"budget", "budget_attainment"),
    (r"share of (donated|retail)", "ecom_share_of_retail"), (r"\bbuyers\b|customers", "buyers"),
]


def route(question: str, ctx: dict, month: str | None = None) -> list[tuple[str, dict]]:
    """Map a question onto tool calls. Empty list = not understood."""
    q = question.lower()
    mo = month or parse_month(question, ctx) or ctx.get("last_complete_month")
    channel = next((c for c, rx in _CHANNEL_WORDS if re.search(rx, q)), None)
    sm = re.search(r"\bstore\s*0?(\d{1,2})\b", q)
    store = f"Store{int(sm.group(1)):02d}" if sm else None
    by_store = bool(re.search(r"which stores?|by store|each store|per store|stores\b|top stores?", q)) and not store
    by_channel = bool(re.search(r"which (channel|marketplace)|by (channel|marketplace)|each (channel|marketplace)|"
                                r"per (channel|marketplace)|channels|marketplaces", q))
    trend = bool(re.search(r"trend|over time|by month|monthly|last \d+ months|past \d+ months", q))
    filt = {k: v for k, v in (("channel", channel), ("store", store)) if v}

    if re.search(r"data quality|\bdq\b|checks? fail|failing|quality (issue|problem)s?|data (issue|problem)s?", q):
        return [("open_dq_failures", {})]
    if re.search(r"\bstores?\b", q) and re.search(r"\bsen[dt]\b|sending|identif|flag|manifest|contribut", q):
        stage = "identified" if re.search(r"identif|flag", q) else "sent"
        return [("items_by_store", {"month": mo, "stage": stage, "limit": 10})]
    if _kpi is not None:
        for rx, kid in _KPI_ALIASES:
            if re.search(rx, q) and kid in _kpi.KPIS:
                if trend:
                    return [("kpi_trend", {"kpi_id": kid, "months": 13, "end_month": mo, **filt})]
                if by_store or by_channel:
                    return [("kpi_breakdown", {"kpi_id": kid, "month": mo, "by": "store" if by_store else "channel",
                                               "top": 10, **filt})]
                return [("kpi_value", {"kpi_id": kid, "month": mo, **filt})]
    if re.search(r"categor", q):
        metric = "units" if re.search(r"\bunits?\b|items sold|volume|how many", q) else "revenue"
        return [("top_categories", {"month": mo, "metric": metric, "limit": 10, **filt})]
    if re.search(r"revenue|sales|sold|sell|made|earn|money|dollars|\$", q):
        if trend and _kpi is not None:
            return [("kpi_trend", {"kpi_id": "total_revenue", "months": 13, "end_month": mo, **filt})]
        by = "store" if by_store else "channel"
        args = {"month": mo, "by": by, **filt}
        if by == "store":
            args["limit"] = 10
        return [("revenue_by", args)]
    return []


def _fmt_val(v: float | None, unit: str) -> str:
    if v is None:
        return "not available"
    if unit == "pct":
        return f"{v * 100:.1f}%"
    if unit in ("usd", "usd_per_hour"):
        return money(v) + (" per labor hour" if unit == "usd_per_hour" else "")
    if unit == "days":
        return f"{v:,.1f} days"
    return f"{v:,.0f}"


def _month_name(m: str | None) -> str:
    if not m:
        return "the latest month"
    d = _month(m)
    return f"{d:%B %Y}"


def describe(result: dict) -> str:
    """Plain-English answer from one tool result (fallback path)."""
    tool, f, data = result["tool"], result["filters"], result.get("data")
    if result.get("error"):
        return f"I couldn't run {tool}: {result['error']}."
    when = _month_name(f.get("month"))
    scope = "".join([f" on {CHANNEL_LABEL.get(f['channel'], f['channel'])}" if f.get("channel") else "",
                     f" for {f['store']}" if f.get("store") else ""])
    if tool == "revenue_by":
        rows = data["rows"]
        if not rows:
            return f"No revenue was recorded{scope} in {when}."
        if f["by"] == "channel" and f.get("channel"):
            r = rows[0]
            return f"Revenue{scope} in {when} was {money(r['revenue'])} from {r['orders']:,} orders."
        label = (lambda k: CHANNEL_LABEL.get(k, k)) if f["by"] == "channel" else (lambda k: k)
        top = ", ".join(f"{label(r['key'])} {money(r['revenue'])}" for r in rows[:5])
        lead = f"In {when}, revenue by {f['by']}{scope}: {top}."
        if f["by"] == "channel":
            lead += f" Total: {money(data['total_revenue_of_rows'])}."
        return lead
    if tool == "top_categories":
        rows = data["rows"]
        if not rows:
            return f"No category sales{scope} in {when}."
        key = data["ranked_by"]
        top = ", ".join(f"{r['key']} ({money(r['revenue'])})" if key == "revenue" else f"{r['key']} ({r['units']:,} units)"
                        for r in rows[:5])
        return f"Top categories by {key}{scope} in {when}: {top}."
    if tool == "items_by_store":
        rows = data["rows"]
        verb = {"identified": "identified for e-commerce", "sent": "sent to e-commerce", "listed": "first listed",
                "sold": "sold"}[data["stage"]]
        if not rows:
            return f"No items were {verb} in {when}."
        top = ", ".join(f"{r['key']} ({r['items']:,})" for r in rows[:5])
        return f"Stores with the most items {verb} in {when}: {top}, out of {data['all_stores_total']:,} items in total."
    if tool == "open_dq_failures":
        rows = data["failing_checks"]
        if not data["checks_run"]:
            return "No data-quality checks have run yet."
        if not rows:
            return f"All {data['checks_run']:,} data-quality checks pass in the latest run."
        items = "; ".join(f"{r['check_id']} ({r['severity']}, {r['failing_rows']:,} rows)" for r in rows[:5])
        return f"{len(rows):,} of {data['checks_run']:,} data-quality checks are failing: {items}."
    if tool == "kpi_value":
        s = f"{data['label']}{scope} in {when} was {_fmt_val(data['value'], data['unit'])}"
        extras = []
        if data.get("prior_month") is not None:
            extras.append(f"prior month {_fmt_val(data['prior_month'], data['unit'])}")
        if data.get("prior_year") is not None:
            extras.append(f"same month last year {_fmt_val(data['prior_year'], data['unit'])}")
        s += f" ({'; '.join(extras)})." if extras else "."
        if data.get("provisional"):
            s += " This value is provisional."
        return s
    if tool == "kpi_trend":
        pts = [p for p in data["points"] if p["value"] is not None]
        if not pts:
            return f"No values for {data['label']}{scope} yet."
        first, last = pts[0], pts[-1]
        return (f"{data['label']}{scope} went from {_fmt_val(first['value'], data['unit'])} in "
                f"{_month_name(first['month'])} to {_fmt_val(last['value'], data['unit'])} in "
                f"{_month_name(last['month'])}.")
    if tool == "kpi_breakdown":
        rows = data["rows"]
        if not rows:
            return f"No {data['label']} values by {data['by']}{scope} in {when}."
        top = ", ".join(f"{CHANNEL_LABEL.get(r['key'], r['key'])} {_fmt_val(r['value'], data['unit'])}" for r in rows[:5])
        return f"{data['label']} by {data['by']}{scope} in {when}, highest first: {top}."
    return json.dumps(data)[:500]


EXAMPLES = ["Which stores sent the most items to e-commerce last month?", "What was sell-through in September?",
            "What was revenue on eBay last month?", "Top categories by revenue this month",
            "Are any data-quality checks failing?"]


def _cite(results: list[dict]) -> list[dict]:
    return [{"tool": r["tool"], "filters": r["filters"], **({"error": r["error"]} if r.get("error") else {})}
            for r in results]


def _fallback_answer(con, question: str, ctx: dict, month: str | None, reason: str) -> dict:
    calls = route(question, ctx, month)
    if not calls:
        ai.log_engine("ask", "fallback", "not understood")
        return {"question": question, "answer": "I can answer questions about revenue by store, channel or category, "
                "items sent by stores, dashboard KPIs (sell-through, net margin, revenue per labor hour...) and "
                "data-quality checks. Try: " + " / ".join(EXAMPLES[:3]),
                "engine": "fallback", "fallback_reason": reason or "question not understood", "citations": [],
                "results": [], "checked_numbers": [], "unmatched_numbers": [], "context": ctx}
    results = [run_tool(con, n, a) for n, a in calls]
    answer = " ".join(describe(r) for r in results)
    chk = check_numbers(answer, {"results": results, "context": ctx})
    ai.log_engine("ask", "fallback", reason)
    return {"question": question, "answer": answer, "engine": "fallback", "fallback_reason": reason,
            "citations": _cite(results), "results": results, "checked_numbers": chk.checked,
            "unmatched_numbers": chk.unmatched, "context": ctx}


# ---------------------------------------------------------------------------------------------------------------
# Claude path
ASK_SYSTEM = """You answer questions about Goodwill's e-commerce data for operations and finance staff.

- Use the provided tools for every number. Never guess or compute figures the tools did not return, except
  simple sums or differences of returned values when the question needs them.
- Prefer one or two tool calls. Months are YYYY-MM; resolve "last month"/"this month" from the data context in
  the user message, not from today's date.
- Answer in 1-3 plain sentences, then a final line "Source: <tool>(<filters>)" for each tool you relied on.
- pct KPI values are fractions: 0.523 means 52.3%.
- Describe the data; do not recommend inventory, pricing, listing or staffing decisions.
- If the tools cannot answer the question, say so and name what data would be needed."""

MAX_TURNS = 6


def _claude_answer(con, question: str, ctx: dict, month: str | None) -> dict | None:
    tools = tool_specs()
    user = (f"Data context: {json.dumps(ctx, sort_keys=True)}"
            + (f"\nThe user is looking at month {month}." if month else "") + f"\n\nQuestion: {question}")
    messages: list[dict] = [{"role": "user", "content": user}]
    results: list[dict] = []
    retried = False
    for _ in range(MAX_TURNS):
        try:
            resp = ai.create("ask", system=ai.cached_system(ASK_SYSTEM), tools=tools, messages=messages,
                             max_tokens=4096, output_config={"effort": "low"})
        except ai.AIUnavailable:
            return None
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "tool_use":
            tool_results = []
            for b in resp.content:
                if getattr(b, "type", None) != "tool_use":
                    continue
                r = run_tool(con, b.name, b.input if isinstance(b.input, dict) else json.loads(b.input))
                results.append(r)
                tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                     "content": json.dumps(r, default=str), **({"is_error": True} if r.get("error") else {})})
            messages.append({"role": "user", "content": tool_results})
            continue
        if resp.stop_reason == "pause_turn":
            continue
        answer = ai.text_of(resp)
        body = re.sub(r"(?im)^\s*source:.*$", "", answer)   # citations restate filters (months, store ids)
        chk = check_numbers(body, {"results": results, "context": ctx, "question": question})
        bad_words = sorted({m.group(0) for m in PRESCRIPTIVE_RE.finditer(body)})
        if (chk.unmatched or bad_words) and not retried:
            retried = True
            probs = []
            if chk.unmatched:
                probs.append("These numbers are not in any tool result: "
                             + ", ".join(u["text"] for u in chk.unmatched) + ".")
            if bad_words:
                probs.append("Don't recommend actions (" + ", ".join(bad_words) + ").")
            messages.append({"role": "user", "content": " ".join(probs) + " Rewrite the answer using only tool results."})
            continue
        if chk.unmatched or bad_words or not answer:
            ai.log.warning("ai[ask] claude answer failed checks twice: %s %s", chk.unmatched, bad_words)
            return {"_failed": "claude answer failed the number check twice"}
        ai.log_engine("ask", "claude", f"{len(results)} tool call(s)")
        return {"question": question, "answer": answer, "engine": "claude", "citations": _cite(results),
                "results": results, "checked_numbers": chk.checked, "unmatched_numbers": [], "context": ctx}
    return {"_failed": "too many tool turns"}


def ask(question: str, month: str | None = None, con: duckdb.DuckDBPyConnection | None = None,
        db_path: Path | str | None = None) -> dict:
    question = (question or "").strip()
    own = con is None
    if own:
        path = Path(db_path or HARMONIZED_PATH)
        if not path.exists():
            raise FileNotFoundError(f"{path} not built yet")
        con = duckdb.connect(str(path), read_only=True)
    try:
        if month:
            month = _month(month).isoformat()[:7]
        ctx = data_context(con)
        reason = "no ANTHROPIC_API_KEY"
        if ai.ai_available():
            got = _claude_answer(con, question, ctx, month)
            if got and "_failed" not in got:
                return got
            reason = got["_failed"] if got else "claude call failed"
        return _fallback_answer(con, question, ctx, month, reason)
    finally:
        if own:
            con.close()
