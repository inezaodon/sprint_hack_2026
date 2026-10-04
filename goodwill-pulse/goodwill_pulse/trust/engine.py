"""Trust engine: compute(metric, filters, breakdown, period) -> the number, the math behind it, and checks on it.

    from goodwill_pulse.trust import compute
    compute(con, "items_sold", filters={"channel": "ShopGoodwill", "department": "Jewelry"}, period="yesterday")

Every number on the dashboard and in the report comes from here, never from the model.

How a result is made
1. Fetch the rows in scope (period + filters) from `sales_line` or `books` (data contract A).
2. Apply the metric's one fixed Python function below, per breakdown group and for the whole.
3. Check the result (attached to the response so the dashboard can show "verified" and "show the math"):
   - recount:            an independent SQL aggregate gets the same value for every group
   - parts_equal_whole:  breakdown values add up to the total (additive metrics only)
   - unpaid_excluded:    rows that aren't paid (or refunds) change nothing
   - refunds_netted:     total = paid − refunds, and refund amounts are negative
   - average_identity:   average sale × orders = total sales
   - departments_platforms_file: Σ by department = Σ by platform = straight file total

Data contract assumptions (confirm with Odin): `status` is 'paid' | 'refund' | anything else (unpaid, cancelled...);
refund rows repeat the order_id with NEGATIVE subtotal/shipping/items on the refund date; `books.week` is the Monday
of the week. An optional `source_file` column on either table feeds "source file" in show-the-math.
"""
from __future__ import annotations

from collections import defaultdict, namedtuple
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable

import duckdb

from .catalog import CATALOG, NOT_CONNECTED, Metric

PAID, REFUND = "paid", "refund"
COUNTED = (PAID, REFUND)
CENT = Decimal("0.01")
TOLERANCE = Decimal("0.005")


class TrustError(ValueError):
    """A request we refuse. str(err) is safe to show a Goodwill user."""


class UnknownMetric(TrustError):
    def __init__(self, metric: str):
        super().__init__("We don't track that yet.")
        self.metric = metric


class NotConnected(TrustError):
    pass


class BadRequest(TrustError):
    pass


def _dec(x) -> Decimal:
    if x is None:
        return Decimal(0)
    return x if isinstance(x, Decimal) else Decimal(str(x))


# ------------------------------------------------------------------------------------------ one function per metric
def _counted_sum(rows, col: str) -> Decimal:
    return sum((_dec(getattr(r, col)) for r in rows if r.status in COUNTED), Decimal(0))


def total_sales(rows) -> Decimal:
    """Σ subtotal of paid lines plus refund lines (refunds are negative, so they net out). Shipping excluded."""
    return _counted_sum(rows, "subtotal")


def gross_sales(rows) -> Decimal:
    """Σ subtotal of paid lines, before refunds: the basis of Goodwill's daily report and Odin's revenue."""
    return sum((_dec(r.subtotal) for r in rows if r.status == PAID), Decimal(0))


def refunds(rows) -> Decimal:
    """Σ refunds issued, as a positive amount (refund rows carry negative subtotals)."""
    return -sum((_dec(r.subtotal) for r in rows if r.status == REFUND), Decimal(0))


def orders(rows) -> Decimal:
    """Distinct paid order IDs. Refund rows are not orders."""
    return Decimal(len({r.order_id for r in rows if r.status == PAID}))


def average_sale(rows) -> Decimal | None:
    """Total sales ÷ orders; None when there are no orders."""
    n = orders(rows)
    return total_sales(rows) / n if n else None


def shipping_charged(rows) -> Decimal:
    """Σ shipping charged to buyers, refunds netted."""
    return _counted_sum(rows, "shipping_charged")


def items_sold(rows) -> Decimal:
    """Σ items, refunds netted."""
    return _counted_sum(rows, "items")


def customers(rows) -> Decimal:
    """Distinct buyers on paid orders. Buyer IDs are per platform, so the key is (channel, buyer_id)."""
    return Decimal(len({(r.channel, r.buyer_id) for r in rows if r.status == PAID}))


def books_sold(rows) -> Decimal:
    return sum((_dec(r.books_sold) for r in rows), Decimal(0))


def books_revenue(rows) -> Decimal:
    return sum((_dec(r.books_revenue) for r in rows), Decimal(0))


def books_scanned(rows) -> Decimal:
    return sum((_dec(r.books_scanned) for r in rows), Decimal(0))


FUNCS: dict[str, Callable] = {
    "total_sales": total_sales, "gross_sales": gross_sales, "refunds": refunds, "orders": orders,
    "average_sale": average_sale,
    "shipping_charged": shipping_charged, "items_sold": items_sold, "customers": customers,
    "books_sold": books_sold, "books_revenue": books_revenue, "books_scanned": books_scanned,
}

# Independent SQL versions of the same formulas, used only by the recount check.
_COUNTED_SQL = "status IN ('paid', 'refund')"
RECOUNT_SQL: dict[str, str] = {
    "total_sales": f"COALESCE(SUM(subtotal) FILTER (WHERE {_COUNTED_SQL}), 0)",
    "gross_sales": "COALESCE(SUM(subtotal) FILTER (WHERE status = 'paid'), 0)",
    "refunds": "-COALESCE(SUM(subtotal) FILTER (WHERE status = 'refund'), 0)",
    "orders": "COUNT(DISTINCT order_id) FILTER (WHERE status = 'paid')",
    "average_sale": f"SUM(subtotal) FILTER (WHERE {_COUNTED_SQL}) "
                    "/ NULLIF(COUNT(DISTINCT order_id) FILTER (WHERE status = 'paid'), 0)",
    "shipping_charged": f"COALESCE(SUM(shipping_charged) FILTER (WHERE {_COUNTED_SQL}), 0)",
    "items_sold": f"COALESCE(SUM(items) FILTER (WHERE {_COUNTED_SQL}), 0)",
    "customers": "COUNT(DISTINCT channel || '|' || buyer_id) FILTER (WHERE status = 'paid')",
    "books_sold": "COALESCE(SUM(books_sold), 0)",
    "books_revenue": "COALESCE(SUM(books_revenue), 0)",
    "books_scanned": "COALESCE(SUM(books_scanned), 0)",
}
_NETTED_COL = {"total_sales": "subtotal", "shipping_charged": "shipping_charged", "items_sold": "items"}

_COLUMNS = {
    "sales_line": ("order_id", "paid_date", "channel", "department", "sku", "origin_store", "items", "subtotal",
                   "shipping_charged", "buyer_id", "status"),
    "books": ("week", "store", "books_scanned", "books_sold", "books_revenue"),
}
_FILTER_COL = {
    "sales_line": {"channel": "channel", "department": "department", "store": "origin_store"},
    "books": {"store": "store"},
}
_GROUP_SQL = {
    "sales_line": {"channel": "channel", "department": "department", "store": "origin_store", "day": "paid_date",
                   "week": "CAST(date_trunc('week', paid_date) AS DATE)",
                   "month": "CAST(date_trunc('month', paid_date) AS DATE)"},
    "books": {"store": "store", "week": "week", "month": "CAST(date_trunc('month', week) AS DATE)"},
}
_TIME_KEYS = ("day", "week", "month")


# ------------------------------------------------------------------------------------------------------- periods
def default_as_of(con: duckdb.DuckDBPyConnection) -> date:
    """'Today' for named periods: the morning after the last day in sales_line (so 'yesterday' = latest day)."""
    for sql in ("SELECT max(paid_date) FROM sales_line", "SELECT max(week) + 7 FROM books"):
        try:
            d = con.execute(sql).fetchone()[0]
        except duckdb.Error:
            continue
        if d is not None:
            return d + timedelta(days=1)
    return date.today()


def resolve_period(period: Any, as_of: date) -> tuple[date, date]:
    """Named period or {"start", "end"} -> inclusive (start, end). Weeks start Monday."""
    if isinstance(period, dict):
        try:
            return date.fromisoformat(str(period["start"])), date.fromisoformat(str(period["end"]))
        except (KeyError, ValueError) as e:
            raise BadRequest(f"Bad period {period!r}: need start and end as YYYY-MM-DD") from e
    p = str(period or "yesterday").lower().replace(" ", "_")
    monday = as_of - timedelta(days=as_of.weekday())
    first = as_of.replace(day=1)
    if p == "today":
        return as_of, as_of
    if p == "yesterday":
        y = as_of - timedelta(days=1)
        return y, y
    if p == "this_week":
        return monday, as_of
    if p == "last_week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if p == "this_month":
        return first, as_of
    if p == "last_month":
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if p.startswith("last_") and p.endswith("_days") and p[5:-5].isdigit():
        return as_of - timedelta(days=int(p[5:-5])), as_of - timedelta(days=1)
    if p.startswith("last_") and p.endswith("_weeks") and p[5:-6].isdigit():
        return monday - timedelta(days=7 * (int(p[5:-6]) - 1)), as_of
    if p == "all":
        return date(1900, 1, 1), as_of
    raise BadRequest(f"Unknown period {period!r}")


# ------------------------------------------------------------------------------------------------------- compute
def get_metric(metric_id: str) -> Metric:
    if metric_id in NOT_CONNECTED:
        label, system = NOT_CONNECTED[metric_id]
        raise NotConnected(f"{label} isn't connected yet; it lives in {system}.")
    if metric_id not in CATALOG:
        raise UnknownMetric(metric_id)
    return CATALOG[metric_id]


def _scope(m: Metric, filters: dict | None, start: date, end: date) -> tuple[str, list]:
    if m.table == "sales_line":
        clauses, params = ["paid_date BETWEEN ? AND ?"], [start, end]
    else:   # a week is in scope when any of its 7 days is
        clauses, params = ["week <= ? AND week + 6 >= ?"], [end, start]
    for dim, val in (filters or {}).items():
        if val in (None, "", []):
            continue
        if dim not in m.filters:
            raise BadRequest(f"{m.label} can't be filtered by {dim}.")
        vals = [str(v).lower() for v in (val if isinstance(val, (list, tuple)) else [val])]
        clauses.append(f"lower({_FILTER_COL[m.table][dim]}) IN ({', '.join('?' * len(vals))})")
        params += vals
    return " AND ".join(clauses), params


def _has_column(con, table: str, col: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
                            [table, col]).fetchone()[0])


def _fetch(con, m: Metric, where: str, params: list, breakdown: str | None) -> tuple[list, list[str]]:
    cols = list(_COLUMNS[m.table])
    has_src = _has_column(con, m.table, "source_file")
    key_sql = _GROUP_SQL[m.table][breakdown] if breakdown else "NULL"
    extra = ", source_file" if has_src else ""
    cur = con.execute(f"SELECT {key_sql} AS group_key, {', '.join(cols)}{extra} FROM {m.table} WHERE {where}", params)
    Row = namedtuple("Row", ["group_key"] + cols + (["source_file"] if has_src else []))
    rows = [Row(*r) for r in cur.fetchall()]
    sources = sorted({r.source_file for r in rows if r.source_file}) if has_src else [m.table]
    return rows, sources


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(_dec(a) - _dec(b)) <= TOLERANCE


def _fmt(m: Metric | str, v) -> str:
    unit = m.unit if isinstance(m, Metric) else m
    if v is None:
        return "—"
    return f"${_dec(v):,.2f}" if unit == "usd" else f"{_dec(v):,.0f}" if _dec(v) == _dec(v).to_integral() \
        else f"{_dec(v):,.2f}"


def _out(m: Metric, v):
    if v is None:
        return None
    v = _dec(v)
    if m.unit == "usd":
        return float(v.quantize(CENT))
    return int(v) if v == v.to_integral() else float(v.quantize(CENT))


def _key_out(k):
    return k.isoformat() if isinstance(k, date) else k


def compute(con: duckdb.DuckDBPyConnection, metric: str, filters: dict | None = None, breakdown: str | None = None,
            period: Any = "yesterday", as_of: date | None = None) -> dict:
    """Value(s) + formula + rows used + source files + checks. Raises TrustError for anything we can't answer."""
    m = get_metric(metric)
    if breakdown and breakdown not in m.breakdowns:
        raise BadRequest(f"{m.label} can't be broken down by {breakdown}.")
    as_of = as_of or default_as_of(con)
    start, end = resolve_period(period, as_of)
    where, params = _scope(m, filters, start, end)
    rows, sources = _fetch(con, m, where, params, breakdown)
    fn = FUNCS[m.id]

    total = fn(rows)
    groups: dict = {}
    if breakdown:
        by_key = defaultdict(list)
        for r in rows:
            by_key[r.group_key].append(r)
        groups = {k: fn(v) for k, v in by_key.items()}

    if breakdown in _TIME_KEYS:
        ordered = sorted(groups.items(), key=lambda kv: kv[0])
    else:
        ordered = sorted(groups.items(), key=lambda kv: (kv[1] is None, -(kv[1] or 0), str(kv[0])))

    counted = [r for r in rows if m.table == "books" or r.status in COUNTED]
    if m.id == "average_sale":
        math = f"{_fmt('usd', FUNCS['total_sales'](rows))} ÷ {_fmt('count', FUNCS['orders'](rows))} orders " \
               f"= {_fmt(m, total)}"
    else:
        math = f"{m.formula} over {len(counted)} rows = {_fmt(m, total)}"

    checks = run_checks(con, m, fn, rows, total, groups, where, params, breakdown)
    return {
        "metric": m.id, "label": m.label, "unit": m.unit, "formula": m.formula, "math": math,
        "period": {"name": period if isinstance(period, str) else "custom",
                   "start": start.isoformat(), "end": end.isoformat()},
        "filters": filters or {}, "breakdown": breakdown,
        "value": _out(m, total),
        "rows": [{"key": _key_out(k), "value": _out(m, v)} for k, v in ordered],
        "row_count": len(counted), "rows_excluded": len(rows) - len(counted),
        "source_files": sources,
        "checks": checks, "verified": all(c["ok"] for c in checks),
    }


# -------------------------------------------------------------------------------------------------------- checks
def _check(cid: str, label: str, ok: bool, detail: str) -> dict:
    return {"id": cid, "label": label, "ok": bool(ok), "detail": detail}


def run_checks(con, m: Metric, fn: Callable, rows: list, total, groups: dict, where: str, params: list,
               breakdown: str | None) -> list[dict]:
    out = []

    # 1. Independent recount in SQL, per group and for the whole.
    key_sql = _GROUP_SQL[m.table][breakdown] if breakdown else None
    if key_sql:
        sql_vals = dict(con.execute(f"SELECT {key_sql} AS k, {RECOUNT_SQL[m.id]} FROM {m.table} "
                                    f"WHERE {where} GROUP BY ALL", params).fetchall())
        bad = [k for k in set(groups) | set(sql_vals) if not _close(groups.get(k), sql_vals.get(k))]
        out.append(_check("recount", "Independent SQL recount matches", not bad,
                          f"{len(groups)} groups match" if not bad else
                          "mismatch for " + ", ".join(f"{_key_out(k)}: {_fmt(m, groups.get(k))} vs "
                                                      f"{_fmt(m, sql_vals.get(k))}" for k in bad[:3])))
    sql_total = con.execute(f"SELECT {RECOUNT_SQL[m.id]} FROM {m.table} WHERE {where}", params).fetchone()[0]
    out.append(_check("recount_total", "Independent SQL recount of the total matches", _close(total, sql_total),
                      f"{_fmt(m, total)} = {_fmt(m, sql_total)}" if _close(total, sql_total)
                      else f"formula says {_fmt(m, total)}, SQL says {_fmt(m, sql_total)}"))

    # 2. Parts add up to the whole.
    if breakdown and breakdown in m.additive_over:
        parts = sum((_dec(v) for v in groups.values()), Decimal(0))
        out.append(_check("parts_equal_whole", f"Sum of {breakdown} values = total", _close(parts, total),
                          f"{_fmt(m, parts)} vs {_fmt(m, total)}"))

    if m.table != "sales_line":
        return out

    # 3. Unpaid (or any non-counted status) rows change nothing.
    clean = [r for r in rows if r.status in COUNTED]
    n_other = len(rows) - len(clean)
    ok = _close(fn(rows), fn(clean))
    out.append(_check("unpaid_excluded", "Unpaid orders don't count", ok,
                      f"{n_other} unpaid/other rows in scope, none counted" if ok
                      else f"{n_other} unpaid/other rows changed the result"))

    # 4. Refunds netted: total = paid + refunds (refunds negative).
    if m.id in _NETTED_COL:
        col = _NETTED_COL[m.id]
        paid = sum((_dec(getattr(r, col)) for r in rows if r.status == PAID), Decimal(0))
        refunds = [_dec(getattr(r, col)) for r in rows if r.status == REFUND]
        ref_sum = sum(refunds, Decimal(0))
        positive = [x for x in refunds if x > 0]
        ok = _close(total, paid + ref_sum) and not positive
        out.append(_check("refunds_netted", "Refunds are subtracted", ok,
                          f"{len(positive)} refund rows have positive amounts" if positive else
                          f"paid {_fmt(m, paid)} − refunds {_fmt(m, -ref_sum)} = {_fmt(m, paid + ref_sum)}"
                          + ("" if ok else f", formula says {_fmt(m, total)}")))

    # 5. Average sale × orders = sales.
    if m.id == "average_sale":
        sales, n = FUNCS["total_sales"](rows), FUNCS["orders"](rows)
        ok = (total is None and n == 0) or (total is not None and _close(_dec(total) * n, sales))
        out.append(_check("average_identity", "Average sale × orders = total sales", ok,
                          f"{_fmt(m, total)} × {_fmt('count', n)} = {_fmt(m, _dec(total) * n if total else 0)} "
                          f"vs {_fmt(m, sales)}"))

    # 6. Department totals = platform totals = straight file total.
    if m.id in _NETTED_COL:
        dims = {}
        for dim in ("department", "channel"):
            by = defaultdict(list)
            for r in rows:
                by[getattr(r, dim)].append(r)
            dims[dim] = sum((_dec(fn(v)) for v in by.values()), Decimal(0))
        col = _NETTED_COL[m.id]
        file_total = _dec(con.execute(f"SELECT COALESCE(SUM({col}) FILTER (WHERE {_COUNTED_SQL}), 0) "
                                      f"FROM {m.table} WHERE {where}", params).fetchone()[0])
        ok = _close(dims["department"], dims["channel"]) and _close(dims["channel"], file_total)
        out.append(_check("departments_platforms_file", "Department totals = platform totals = file total", ok,
                          f"{_fmt(m, dims['department'])} = {_fmt(m, dims['channel'])} = {_fmt(m, file_total)}"))
    return out


def dataset_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Checks on the whole sales_line table (not one answer): rows the adapter should have sent to exceptions."""
    out = []
    dupes = con.execute("SELECT order_id, sku, status, count(*) FROM sales_line WHERE status = 'paid' "
                        "GROUP BY ALL HAVING count(*) > 1").fetchall()
    out.append(_check("no_duplicate_lines", "No duplicate order lines", not dupes,
                      "none" if not dupes else f"{len(dupes)} duplicates, e.g. order {dupes[0][0]} sku {dupes[0][1]}"))
    missing = con.execute(f"SELECT count(*) FROM sales_line WHERE {_COUNTED_SQL} AND "
                          "(department IS NULL OR channel IS NULL OR department = '' OR channel = '')").fetchone()[0]
    out.append(_check("no_missing_department", "Every counted line has a department and platform", not missing,
                      f"{missing} lines missing one"))
    positive = con.execute("SELECT count(*) FROM sales_line WHERE status = 'refund' AND subtotal > 0").fetchone()[0]
    out.append(_check("refunds_negative", "Refund lines carry negative amounts", not positive,
                      f"{positive} refund lines are positive"))
    return out


def dimension_values(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Distinct filter values (exact spellings) for the intelligence engine prompt."""
    q = lambda sql: [r[0] for r in con.execute(sql).fetchall() if r[0] is not None]
    return {
        "channel": q("SELECT DISTINCT channel FROM sales_line ORDER BY 1"),
        "department": q("SELECT DISTINCT department FROM sales_line ORDER BY 1"),
        "store": q("SELECT DISTINCT origin_store FROM sales_line UNION SELECT DISTINCT store FROM books ORDER BY 1"),
    }
