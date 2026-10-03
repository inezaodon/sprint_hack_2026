"""Monthly e-commerce KPIs over data/harmonized.duckdb (docs/CONTRACT.md section 4).

Every KPI is one documented function in this module, registered in ``KPIS``. The functions are pure
(read-only SQL against the harmonized model, no globals mutated) and *month-vectorized*: each takes a
``_Ctx`` (connection + filters + per-call memo) and an inclusive month range and returns
``{month_start: value | None}``. That lets ``kpi_card`` get the 13-month trend, the prior month and the
prior year in one query per building block instead of 15 separate queries.

Conventions (shared with the dashboard and AI engineers):

* ``month`` is any ``date`` inside the month; months are calendar months in America/New_York
  (``business_date`` for orders/refunds/labor, ``*_utc`` timestamps converted to New York for listings/items).
* Units: ``pct`` values are **fractions** (0.1234 = 12.34 %); ``usd`` in dollars; ``days`` in decimal days;
  ``count`` as a float; ``usd_per_hour`` in dollars per labor hour.
* Missing data -> ``None``, never 0. A month is "covered" by a table when the unfiltered table has any rows in
  that month; inside a covered month an empty filter result is a genuine 0 for sums/counts, and a ratio with a
  zero denominator is ``None``.
* Filters: ``store`` credits revenue to the originating store via ``fct_order_lines.store_id`` (inventory via
  ``fct_listings.store_id`` / ``fct_items.store_id``); ``channel`` filters on the channel column of each fact.
  ``category`` (an extra optional filter, not in the contract) uses the canonical category on lines/listings/items.
* Labor and overhead are not store- or channel-attributable. Under a filter they are allocated:
  labor (hours, cost, FTE) by the filter's share of **listings created** in the month (listing is what the
  e-com staff spend their hours on); overhead by the filter's share of **revenue**. See docs/KPI_DEFINITIONS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb

from .config import DATA_DIR

HARMONIZED_PATH = DATA_DIR / "harmonized.duckdb"
TZ = "America/New_York"
FTE_HOURS_PER_MONTH = 2080 / 12          # 40 h x 52 wk / 12 = 173.33 h = 1 FTE-month
SELL_THROUGH_WINDOW_DAYS = 30
SELL_THROUGH_TARGET = (0.50, 0.55)       # store target (slide 36); e-com target unknown
WATCH_BAND_PCT_POINTS = 0.01             # pct KPIs: within 1 point of last year (wrong way) -> watch
WATCH_BAND_REL = 0.05                    # other KPIs: within 5 % of last year (wrong way) -> watch

PILLARS = ("growth", "profitability", "productivity", "inventory", "engagement")
ANCHORS = ("net_margin", "revenue_per_labor_hour", "sell_through")
# Slide 35 COO scorecard, 5 rows x 3 in reading order.
SCORECARD = (
    "total_revenue", "revenue_growth_yoy", "net_margin",                         # Financial
    "revenue_per_labor_hour", "listings_created", "listings_per_employee",       # Productivity
    "sell_through", "days_donation_to_listing", "unlisted_backlog",              # Inventory
    "unsold_pct", "avg_selling_price", "sales_per_employee",                     # Sales
    "top_categories_by_revenue", "top_categories_by_margin", "repeat_buyer_rate",  # Category + Customer
)
SCORECARD_ROWS = ("Financial", "Productivity", "Inventory", "Sales", "Category + Customer")


# ----------------------------------------------------------------------------------------------- helpers
def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _months(start: date, end: date) -> list[date]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = add_months(m, 1)
    return out


def _f(x) -> float | None:
    return None if x is None else float(x)


def _div(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return float(a) / float(b)


def _ny_month(col: str) -> str:
    return f"date_trunc('month', ({col} AT TIME ZONE '{TZ}'))::DATE"


def _d_month(col: str) -> str:
    return f"date_trunc('month', {col})::DATE"


@dataclass(frozen=True)
class Filters:
    store: str | None = None
    channel: str | None = None
    category: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.store or self.channel or self.category)

    def where(self, store_col: str | None, channel_col: str | None, category_col: str | None,
              prefix: str = "AND") -> tuple[str, list]:
        """SQL predicate + params. Raises _NotAttributable if a set filter has no column here."""
        parts, params = [], []
        for val, col in ((self.store, store_col), (self.channel, channel_col), (self.category, category_col)):
            if val is None:
                continue
            if col is None:
                raise _NotAttributable
            parts.append(f"{col} = ?")
            params.append(val)
        return ((f" {prefix} " + " AND ".join(parts)) if parts else ""), params


class _NotAttributable(Exception):
    """A filter was applied to a fact that has no such dimension (e.g. channel on the unlisted backlog)."""


class _Ctx:
    """Connection + filters + memo shared by every KPI evaluated in one compute/card/scorecard call."""

    def __init__(self, con, f: Filters, memo: dict | None = None):
        self.con, self.f = con, f
        self.memo = memo if memo is not None else {}

    def with_filters(self, f: Filters) -> "_Ctx":
        return _Ctx(self.con, f, self.memo)

    def cached(self, key, fn):
        k = (key, self.f)
        if k not in self.memo:
            self.memo[k] = fn()
        return self.memo[k]

    def rows(self, sql: str, params: list | None = None) -> list[tuple]:
        return self.con.execute(sql, params or []).fetchall()

    def covered(self, name: str, sql: str) -> set[date]:
        """Months in which an (unfiltered) table has rows. ``sql`` must select one DATE column."""
        k = ("covered", name)
        if k not in self.memo:
            self.memo[k] = {r[0] for r in self.rows(sql) if r[0] is not None}
        return self.memo[k]

    def as_of(self) -> date | None:
        """Last business date with orders: the 'data through' date."""
        k = ("as_of",)
        if k not in self.memo:
            self.memo[k] = self.rows("SELECT max(business_date) FROM fct_orders")[0][0]
        return self.memo[k]

    def has_rows(self, name: str, sql: str) -> bool:
        k = ("has", name)
        if k not in self.memo:
            self.memo[k] = bool(self.rows(sql)[0][0])
        return self.memo[k]


def _grid(ctx: _Ctx, start: date, end: date, covered: set[date], got: dict, default=0.0) -> dict:
    """Spread query results over the month range: covered & absent -> default, uncovered -> None."""
    return {m: (got.get(m, default) if m in covered else None) for m in _months(start, end)}


# ---------------------------------------------------------------------------------------- coverage
def _cov_orders(ctx):
    return ctx.covered("orders", f"SELECT DISTINCT {_d_month('business_date')} FROM fct_orders")


def _cov_listed(ctx):
    return ctx.covered("listed", f"SELECT DISTINCT {_ny_month('listed_at_utc')} FROM fct_listings")


def _cov_ended(ctx):
    return ctx.covered("ended", f"SELECT DISTINCT {_ny_month('ended_at_utc')} FROM fct_listings "
                                "WHERE ended_at_utc IS NOT NULL")


def _cov_labor(ctx):
    return ctx.covered("labor", f"SELECT DISTINCT {_d_month('work_date')} FROM fct_labor")


def _cov_items(ctx, col):
    return ctx.covered(f"items.{col}", f"SELECT DISTINCT {_ny_month(col)} FROM fct_items")


# --------------------------------------------------------------------------------- building blocks
def _b_sales(ctx: _Ctx, start, end) -> dict:
    """{m: (revenue, marketplace_fees)}. Unfiltered/channel: fct_orders.subtotal & marketplace_fees (Daily Pulse
    basis). Store/category: fct_order_lines.sale_price & fee_alloc (the originating store gets the credit)."""
    def run():
        f = ctx.f
        rng = [start, add_months(end, 1)]
        if f.store or f.category:
            w, p = f.where("store_id", "channel", "category")
            sql = (f"SELECT {_d_month('business_date')} m, SUM(sale_price), SUM(fee_alloc) FROM fct_order_lines "
                   f"WHERE business_date >= ? AND business_date < ? {w} GROUP BY 1")
        else:
            w, p = f.where(None, "channel", None)
            sql = (f"SELECT {_d_month('business_date')} m, SUM(subtotal), SUM(marketplace_fees) FROM fct_orders "
                   f"WHERE business_date >= ? AND business_date < ? {w} GROUP BY 1")
        got = {r[0]: (_f(r[1]) or 0.0, _f(r[2]) or 0.0) for r in ctx.rows(sql, rng + p)}
        return _grid(ctx, start, end, _cov_orders(ctx), got, (0.0, 0.0))
    return ctx.cached(("sales", start, end), run)


def _order_share_sql(f: Filters) -> tuple[str, list]:
    """Per-order share of sale_price matching a store/category filter (for allocating order-level amounts)."""
    w, p = f.where("store_id", "channel", "category", prefix="AND")
    cond = w[len(" AND "):] if w else "TRUE"
    return (f"SELECT order_key, SUM(CASE WHEN {cond} THEN sale_price ELSE 0 END) / NULLIF(SUM(sale_price), 0) "
            f"AS share FROM fct_order_lines GROUP BY order_key"), p


def _b_refunds(ctx: _Ctx, start, end) -> dict:
    """{m: refunds $} by refund business_date (cash basis). Under a store/category filter each refund is allocated
    to lines pro rata to sale_price within its order."""
    def run():
        f = ctx.f
        rng = [start, add_months(end, 1)]
        if f.store or f.category:
            share_sql, sp = _order_share_sql(f)
            sql = (f"WITH s AS ({share_sql}) SELECT {_d_month('r.business_date')} m, SUM(r.amount * s.share) "
                   f"FROM fct_refunds r JOIN s USING (order_key) "
                   f"WHERE r.business_date >= ? AND r.business_date < ? GROUP BY 1")
            params = sp + rng
        else:
            w, p = f.where(None, "channel", None)
            sql = (f"SELECT {_d_month('business_date')} m, SUM(amount) FROM fct_refunds "
                   f"WHERE business_date >= ? AND business_date < ? {w} GROUP BY 1")
            params = rng + p
        got = {r[0]: _f(r[1]) or 0.0 for r in ctx.rows(sql, params)}
        return _grid(ctx, start, end, _cov_orders(ctx), got)   # a month with orders and no refunds = $0
    return ctx.cached(("refunds", start, end), run)


SHIPPING_LABEL_FEE_TYPES = ("shipping_label",)


def _shipping_labels_available(ctx: _Ctx) -> bool:
    types = ", ".join(f"'{t}'" for t in SHIPPING_LABEL_FEE_TYPES)
    return ctx.has_rows("ship_labels", f"SELECT count(*) FROM fct_fees WHERE fee_type IN ({types})")


def _b_shipping_labels(ctx: _Ctx, start, end) -> dict:
    """{m: shipping label cost $} from fct_fees rows with fee_type 'shipping_label' (if the harmonizer emits
    them). When the model has none at all, the component is 0 and margin cards carry a note."""
    def run():
        if not _shipping_labels_available(ctx):
            return _grid(ctx, start, end, _cov_orders(ctx), {})
        f = ctx.f
        types = ", ".join(f"'{t}'" for t in SHIPPING_LABEL_FEE_TYPES)
        rng = [start, add_months(end, 1)]
        if f.store or f.category:
            share_sql, sp = _order_share_sql(f)
            sql = (f"WITH s AS ({share_sql}) SELECT {_d_month('x.business_date')} m, SUM(x.amount * s.share) "
                   f"FROM fct_fees x JOIN s USING (order_key) WHERE x.fee_type IN ({types}) "
                   f"AND x.business_date >= ? AND x.business_date < ? GROUP BY 1")
            params = sp + rng
        else:
            w, p = f.where(None, "channel", None)
            sql = (f"SELECT {_d_month('business_date')} m, SUM(amount) FROM fct_fees WHERE fee_type IN ({types}) "
                   f"AND business_date >= ? AND business_date < ? {w} GROUP BY 1")
            params = rng + p
        got = {r[0]: _f(r[1]) or 0.0 for r in ctx.rows(sql, params)}
        return _grid(ctx, start, end, _cov_orders(ctx), got)
    return ctx.cached(("ship", start, end), run)


def _b_lines(ctx: _Ctx, start, end) -> dict:
    """{m: (revenue, units, median unit price)} from fct_order_lines with every filter."""
    def run():
        w, p = ctx.f.where("store_id", "channel", "category")
        sql = (f"SELECT {_d_month('business_date')} m, SUM(sale_price), SUM(quantity), "
               f"median(sale_price / NULLIF(quantity, 0)) FROM fct_order_lines "
               f"WHERE business_date >= ? AND business_date < ? {w} GROUP BY 1")
        got = {r[0]: (_f(r[1]) or 0.0, _f(r[2]) or 0.0, _f(r[3]))
               for r in ctx.rows(sql, [start, add_months(end, 1)] + p)}
        return _grid(ctx, start, end, _cov_orders(ctx), got, (0.0, 0.0, None))
    return ctx.cached(("lines", start, end), run)


def _b_labor_total(ctx: _Ctx, start, end) -> dict:
    """{m: (hours, cost, lister_hours)} for ALL e-com staff (fct_labor has no store/channel)."""
    def run():
        sql = (f"SELECT {_d_month('work_date')} m, SUM(hours), SUM(cost), "
               f"SUM(hours) FILTER (WHERE role = 'lister') FROM fct_labor "
               f"WHERE work_date >= ? AND work_date < ? GROUP BY 1")
        got = {r[0]: (_f(r[1]) or 0.0, _f(r[2]), _f(r[3]) or 0.0)
               for r in ctx.rows(sql, [start, add_months(end, 1)])}
        return _grid(ctx, start, end, _cov_labor(ctx), got, (0.0, None, 0.0))
    return ctx.with_filters(Filters()).cached(("labor", start, end), run)


def _b_labor_share(ctx: _Ctx, start, end) -> dict:
    """{m: share of labor attributable to the filter} = filtered listings created / all listings created."""
    if not ctx.f.any:
        return {m: 1.0 for m in _months(start, end)}
    mine = _b_first_listings(ctx, start, end)
    allx = _b_first_listings(ctx.with_filters(Filters()), start, end)
    return {m: (_div(mine[m]["n"], allx[m]["n"]) if mine[m] and allx[m] else None) for m in mine}


def _b_labor(ctx: _Ctx, start, end) -> dict:
    """{m: (hours, cost, lister_hours)} allocated to the current filter (see _b_labor_share)."""
    tot, share = _b_labor_total(ctx, start, end), _b_labor_share(ctx, start, end)
    out = {}
    for m in tot:
        t, s = tot[m], share[m]
        if t is None or s is None:
            out[m] = None
        else:
            out[m] = (t[0] * s, None if t[1] is None else t[1] * s, t[2] * s)
    return out


def _b_inputs(ctx: _Ctx, start, end) -> dict:
    """{m: (overhead_allocation, store_retail_revenue)} from the monthly manual inputs (None if not entered)."""
    def run():
        got = {r[0]: (_f(r[1]), _f(r[2])) for r in ctx.rows(
            f"SELECT {_d_month('month')}, overhead_allocation, store_retail_revenue FROM fct_monthly_inputs "
            f"WHERE month >= ? AND month < ?", [start, add_months(end, 1)])}
        return {m: got.get(m, (None, None)) for m in _months(start, end)}
    return ctx.with_filters(Filters()).cached(("inputs", start, end), run)


_FIRST_LISTING_CTE = f"""
first_l AS (
    SELECT sku, min(listed_at_utc) AS fl,
           arg_min(channel, listed_at_utc) AS channel, arg_min(store_id, listed_at_utc) AS store_id,
           arg_min(category, listed_at_utc) AS category
    FROM fct_listings WHERE sku IS NOT NULL AND listed_at_utc IS NOT NULL GROUP BY sku),
first_sale AS (
    SELECT l.sku, min(o.paid_at_utc) AS sold_at
    FROM fct_order_lines l JOIN fct_orders o USING (order_key) WHERE l.sku IS NOT NULL GROUP BY l.sku)
"""


def _b_first_listings(ctx: _Ctx, start, end) -> dict:
    """{m: {n, sold30, matured, days_ident, days_manifest}} for the cohort of items whose FIRST listing (any
    channel, earliest fct_listings.listed_at_utc per sku) falls in month m (New York time). Store/channel/category
    are those of the first listing. sold30 = sold (first order line for the sku, any channel) within 30 days."""
    def run():
        w, p = ctx.f.where("fl_.store_id", "fl_.channel", "fl_.category")
        sql = f"""WITH {_FIRST_LISTING_CTE}
            SELECT {_ny_month('fl_.fl')} m, count(*),
                   count(*) FILTER (WHERE s.sold_at >= fl_.fl
                                    AND s.sold_at <= fl_.fl + INTERVAL {SELL_THROUGH_WINDOW_DAYS} DAY),
                   avg(epoch(fl_.fl - i.identified_at_utc)) / 86400.0,
                   avg(epoch(fl_.fl - i.manifested_at_utc)) / 86400.0,
                   max(fl_.fl)
            FROM first_l fl_ LEFT JOIN first_sale s USING (sku) LEFT JOIN fct_items i ON i.item_id = fl_.sku
            WHERE {_ny_month('fl_.fl')} BETWEEN ? AND ? {w}
            GROUP BY 1"""
        got = {r[0]: {"n": r[1], "sold30": r[2], "days_ident": _f(r[3]), "days_manifest": _f(r[4]),
                      "last_listed": r[5]} for r in ctx.rows(sql, [start, end] + p)}
        empty = {"n": 0, "sold30": 0, "days_ident": None, "days_manifest": None, "last_listed": None}
        return _grid(ctx, start, end, _cov_listed(ctx), got, empty)
    return ctx.cached(("first_listings", start, end), run)


def _b_listing_ends(ctx: _Ctx, start, end) -> dict:
    """{m: (ended, unsold)}: listings (every listing incl. relists) whose ended_at falls in month m."""
    def run():
        w, p = ctx.f.where("store_id", "channel", "category")
        sql = (f"SELECT {_ny_month('ended_at_utc')} m, count(*) FILTER (WHERE status IN ('sold','unsold')), "
               f"count(*) FILTER (WHERE status = 'unsold') FROM fct_listings "
               f"WHERE ended_at_utc IS NOT NULL AND {_ny_month('ended_at_utc')} BETWEEN ? AND ? {w} GROUP BY 1")
        got = {r[0]: (r[1], r[2]) for r in ctx.rows(sql, [start, end] + p)}
        return _grid(ctx, start, end, _cov_ended(ctx), got, (0, 0))
    return ctx.cached(("ends", start, end), run)


def _b_listing_starts(ctx: _Ctx, start, end) -> dict:
    """{m: (distinct skus listed in m, distinct skus with a relist listed in m)}."""
    def run():
        w, p = ctx.f.where("store_id", "channel", "category")
        sql = (f"SELECT {_ny_month('listed_at_utc')} m, count(DISTINCT sku), "
               f"count(DISTINCT sku) FILTER (WHERE relist_of IS NOT NULL) FROM fct_listings "
               f"WHERE {_ny_month('listed_at_utc')} BETWEEN ? AND ? {w} GROUP BY 1")
        got = {r[0]: (r[1], r[2]) for r in ctx.rows(sql, [start, end] + p)}
        return _grid(ctx, start, end, _cov_listed(ctx), got, (0, 0))
    return ctx.cached(("starts", start, end), run)


def _b_sales_timing(ctx: _Ctx, start, end) -> dict:
    """{m: avg days from first listing to first sale} for skus whose first sale is in month m (NY). Filters use the
    selling order line's store/channel/category."""
    def run():
        w, p = ctx.f.where("s.store_id", "s.channel", "s.category")
        sql = f"""WITH {_FIRST_LISTING_CTE},
            sale AS (SELECT l.sku, arg_min(l.store_id, o.paid_at_utc) store_id, arg_min(l.channel, o.paid_at_utc) channel,
                            arg_min(l.category, o.paid_at_utc) category, min(o.paid_at_utc) sold_at
                     FROM fct_order_lines l JOIN fct_orders o USING (order_key) WHERE l.sku IS NOT NULL GROUP BY l.sku)
            SELECT {_ny_month('s.sold_at')} m, avg(epoch(s.sold_at - fl_.fl)) / 86400.0
            FROM sale s JOIN first_l fl_ USING (sku)
            WHERE {_ny_month('s.sold_at')} BETWEEN ? AND ? {w} GROUP BY 1"""
        got = {r[0]: _f(r[1]) for r in ctx.rows(sql, [start, end] + p)}
        return _grid(ctx, start, end, _cov_orders(ctx), got, None)
    return ctx.cached(("timing", start, end), run)


def _b_buyers(ctx: _Ctx, start, end) -> dict:
    """{m: (buyers, repeat, new)}. A buyer is repeat in m when buyer_key has ANY order (any store) with
    business_date before the 1st of m; new when the first ever order is in m. buyer_key is channel-scoped."""
    def run():
        f = ctx.f
        if f.store or f.category:
            w, p = f.where("l.store_id", "l.channel", "l.category")
            mb = (f"SELECT DISTINCT {_d_month('l.business_date')} m, o.buyer_key FROM fct_order_lines l "
                  f"JOIN fct_orders o USING (order_key) WHERE o.buyer_key IS NOT NULL "
                  f"AND l.business_date >= ? AND l.business_date < ? {w}")
        else:
            w, p = f.where(None, "channel", None)
            mb = (f"SELECT DISTINCT {_d_month('business_date')} m, buyer_key FROM fct_orders "
                  f"WHERE buyer_key IS NOT NULL AND business_date >= ? AND business_date < ? {w}")
        sql = f"""WITH firsts AS (SELECT buyer_key, min(business_date) fd FROM fct_orders
                                  WHERE buyer_key IS NOT NULL GROUP BY 1),
                       mb AS ({mb})
                  SELECT m, count(*), count(*) FILTER (WHERE fd < m), count(*) FILTER (WHERE fd >= m)
                  FROM mb JOIN firsts USING (buyer_key) GROUP BY m"""
        got = {r[0]: (r[1], r[2], r[3]) for r in ctx.rows(sql, [start, add_months(end, 1)] + p)}
        return _grid(ctx, start, end, _cov_orders(ctx), got, (0, 0, 0))
    return ctx.cached(("buyers", start, end), run)


def _b_items_flow(ctx: _Ctx, start, end, col: str) -> dict:
    """{m: items whose <col> (identified_at_utc / manifested_at_utc) is in month m}. No channel dimension."""
    def run():
        w, p = ctx.f.where("store_id", None, "category")
        sql = (f"SELECT {_ny_month(col)} m, count(*) FROM fct_items WHERE {col} IS NOT NULL "
               f"AND {_ny_month(col)} BETWEEN ? AND ? {w} GROUP BY 1")
        got = {r[0]: r[1] for r in ctx.rows(sql, [start, end] + p)}
        return _grid(ctx, start, end, _cov_items(ctx, col), got, 0)
    return ctx.cached(("flow", col, start, end), run)


def _b_backlog(ctx: _Ctx, start, end) -> dict:
    """{m: items manifested (sent to e-com) before the end of m and not first-listed by the end of m}."""
    def run():
        w, p = ctx.f.where("store_id", None, "category")
        cov = _cov_items(ctx, "manifested_at_utc")
        out = {}
        months = _months(start, end)
        ends = [(m, add_months(m, 1)) for m in months]
        # one query: cross join the month ends against items (150k x 13 is fine in DuckDB)
        vals = ", ".join(f"(DATE '{m.isoformat()}', DATE '{e.isoformat()}')" for m, e in ends)
        sql = f"""WITH me(m, e) AS (VALUES {vals}),
                       it AS (SELECT (manifested_at_utc AT TIME ZONE '{TZ}') man,
                                     (first_listed_at_utc AT TIME ZONE '{TZ}') fl
                              FROM fct_items WHERE manifested_at_utc IS NOT NULL {w})
                  SELECT me.m, count(it.man) FROM me LEFT JOIN it
                    ON it.man < me.e AND (it.fl IS NULL OR it.fl >= me.e) GROUP BY me.m"""
        got = {r[0]: r[1] for r in ctx.rows(sql, p)}
        first_cov = min(cov) if cov else None
        for m in months:
            # defined once item data starts; months after the data window (no manifests yet) -> None
            out[m] = got.get(m, 0) if (first_cov and first_cov <= m <= max(cov)) else None
        return out
    return ctx.cached(("backlog", start, end), run)


def _b_budget(ctx: _Ctx, start, end) -> dict:
    """{m: revenue budget $} summed over channels (or one channel). No store dimension."""
    def run():
        w, p = ctx.f.where(None, "channel", None)
        got = {r[0]: _f(r[1]) for r in ctx.rows(
            f"SELECT {_d_month('month')} m, SUM(revenue_budget) FROM fct_budget WHERE month >= ? AND month < ? "
            f"{w} GROUP BY 1", [start, add_months(end, 1)] + p)}
        return {m: got.get(m) for m in _months(start, end)}
    return ctx.cached(("budget", start, end), run)


def _categories(ctx: _Ctx) -> list[str]:
    if ctx.f.category:
        return [ctx.f.category]
    k = ("categories",)
    if k not in ctx.memo:
        ctx.memo[k] = [r[0] for r in ctx.rows(
            "SELECT DISTINCT category FROM fct_order_lines WHERE category IS NOT NULL ORDER BY 1")]
    return ctx.memo[k]


def _map(months: list[date], fn) -> dict:
    return {m: fn(m) for m in months}


# ===================================================================================== KPI functions
# Each returns {month: value | None} over the inclusive month range [start, end].

# --- Growth ---------------------------------------------------------------------------------------
def total_revenue(ctx: _Ctx, start: date, end: date) -> dict:
    """Total e-commerce revenue: Σ item subtotal (fct_orders.subtotal; under a store/category filter Σ
    fct_order_lines.sale_price credited to the originating store), excl. shipping, handling and tax."""
    s = _b_sales(ctx, start, end)
    return {m: (None if v is None else round(v[0], 2)) for m, v in s.items()}


def revenue_growth_yoy(ctx: _Ctx, start: date, end: date) -> dict:
    """Revenue growth % YoY: (revenue(m) − revenue(m−12)) ÷ revenue(m−12). None without the prior-year month."""
    rev = total_revenue(ctx, add_months(start, -12), end)
    return _map(_months(start, end), lambda m: (
        None if rev[m] is None else _div(rev[m] - rev[add_months(m, -12)], rev[add_months(m, -12)])
        if rev[add_months(m, -12)] is not None else None))


def budget_attainment(ctx: _Ctx, start: date, end: date) -> dict:
    """Budget vs actual: revenue ÷ revenue budget (fct_budget, by channel). Budget has no store/category, so
    None under those filters."""
    rev, bud = total_revenue(ctx, start, end), _b_budget(ctx, start, end)
    return _map(_months(start, end), lambda m: _div(rev[m], bud[m]))


def ecom_share_of_retail(ctx: _Ctx, start: date, end: date) -> dict:
    """E-com share of donated-goods retail: e-com revenue ÷ (e-com revenue + store retail revenue). Store retail
    is an all-stores monthly input, so this is company-level only (None under any filter)."""
    if ctx.f.any:
        raise _NotAttributable
    rev, inp = total_revenue(ctx, start, end), _b_inputs(ctx, start, end)
    return _map(_months(start, end), lambda m: (
        None if rev[m] is None or inp[m][1] is None else _div(rev[m], rev[m] + inp[m][1])))


# --- Profitability --------------------------------------------------------------------------------
def gross_profit(ctx: _Ctx, start: date, end: date) -> dict:
    """Gross profit $: revenue − marketplace fees − shipping label cost (if in the model) − refunds.
    Donated goods have no purchase cost."""
    s, r, sh = _b_sales(ctx, start, end), _b_refunds(ctx, start, end), _b_shipping_labels(ctx, start, end)
    return _map(_months(start, end), lambda m: (
        None if s[m] is None or r[m] is None or sh[m] is None else round(s[m][0] - s[m][1] - sh[m] - r[m], 2)))


def gross_margin(ctx: _Ctx, start: date, end: date) -> dict:
    """Gross margin %: gross profit ÷ revenue."""
    gp, rev = gross_profit(ctx, start, end), total_revenue(ctx, start, end)
    return _map(_months(start, end), lambda m: _div(gp[m], rev[m]))


def net_profit(ctx: _Ctx, start: date, end: date) -> dict:
    """Net profit $: gross profit − e-com labor cost − allocated overhead. Labor is allocated to a filter by its
    share of listings created, overhead by its share of revenue. None if labor or overhead is missing."""
    gp, lab, inp = gross_profit(ctx, start, end), _b_labor(ctx, start, end), _b_inputs(ctx, start, end)
    rev = total_revenue(ctx, start, end)
    all_rev = total_revenue(ctx.with_filters(Filters()), start, end) if ctx.f.any else rev

    def one(m):
        if gp[m] is None or lab[m] is None or lab[m][1] is None or inp[m][0] is None:
            return None
        oh_share = 1.0 if not ctx.f.any else _div(rev[m], all_rev[m])
        if oh_share is None:
            return None
        return round(gp[m] - lab[m][1] - inp[m][0] * oh_share, 2)
    return _map(_months(start, end), one)


def net_margin(ctx: _Ctx, start: date, end: date) -> dict:
    """★ E-com net margin %: (revenue − marketplace fees − shipping label cost − refunds − e-com labor cost −
    allocated overhead) ÷ revenue."""
    npf, rev = net_profit(ctx, start, end), total_revenue(ctx, start, end)
    return _map(_months(start, end), lambda m: _div(npf[m], rev[m]))


def profit_per_labor_hour(ctx: _Ctx, start: date, end: date) -> dict:
    """Profit per labor hour: (gross profit − labor cost) ÷ labor hours (allocated under a filter)."""
    gp, lab = gross_profit(ctx, start, end), _b_labor(ctx, start, end)
    return _map(_months(start, end), lambda m: (
        None if gp[m] is None or lab[m] is None or lab[m][1] is None else _div(gp[m] - lab[m][1], lab[m][0])))


def _top_category(base: Callable, ctx: _Ctx, start: date, end: date) -> dict:
    per_cat = {c: base(ctx.with_filters(replace(ctx.f, category=c)), start, end) for c in _categories(ctx)}
    def one(m):
        vals = [v[m] for v in per_cat.values() if v[m] is not None]
        return max(vals) if vals else None
    return _map(_months(start, end), one)


def top_categories_by_margin(ctx: _Ctx, start: date, end: date) -> dict:
    """Top 10 categories by margin: categories ranked by gross profit $ (revenue − fees − shipping labels −
    refunds, lines credited by category). Value = gross profit of the #1 category; the card carries the ranked
    top-10 list in ``breakdown``."""
    return _top_category(gross_profit, ctx, start, end)


# --- Productivity ---------------------------------------------------------------------------------
def revenue_per_labor_hour(ctx: _Ctx, start: date, end: date) -> dict:
    """★ Revenue per labor hour: revenue ÷ e-com labor hours (all fct_labor roles), same month. Under a filter,
    hours are allocated by the filter's share of listings created."""
    rev, lab = total_revenue(ctx, start, end), _b_labor(ctx, start, end)
    return _map(_months(start, end), lambda m: None if lab[m] is None else _div(rev[m], lab[m][0]))


def listings_created(ctx: _Ctx, start: date, end: date) -> dict:
    """Listings created: items whose FIRST listing (any channel) started in the month; relists not counted."""
    fl = _b_first_listings(ctx, start, end)
    return {m: (None if v is None else float(v["n"])) for m, v in fl.items()}


def listings_per_day(ctx: _Ctx, start: date, end: date) -> dict:
    """Listings created per working day: listings created ÷ days with any e-com labor hours in the month."""
    lc = listings_created(ctx, start, end)
    def run():
        return {r[0]: r[1] for r in ctx.rows(
            f"SELECT {_d_month('work_date')} m, count(DISTINCT work_date) FROM fct_labor WHERE hours > 0 "
            f"AND work_date >= ? AND work_date < ? GROUP BY 1", [start, add_months(end, 1)])}
    days = ctx.with_filters(Filters()).cached(("workdays", start, end), run)
    return _map(_months(start, end), lambda m: _div(lc[m], days.get(m)))


def listings_per_employee(ctx: _Ctx, start: date, end: date) -> dict:
    """Listings per employee: listings created ÷ lister FTE (lister hours ÷ 173.33). Lister FTE is allocated by
    listing share under a filter, so filtered values equal the company value (not store-attributable)."""
    lc, lab = listings_created(ctx, start, end), _b_labor(ctx, start, end)
    return _map(_months(start, end), lambda m: (
        None if lab[m] is None else _div(lc[m], lab[m][2] / FTE_HOURS_PER_MONTH)))


def sales_per_employee(ctx: _Ctx, start: date, end: date) -> dict:
    """Sales per employee: revenue ÷ e-com FTE (all roles' hours ÷ 173.33; allocated under a filter)."""
    rev, lab = total_revenue(ctx, start, end), _b_labor(ctx, start, end)
    return _map(_months(start, end), lambda m: (
        None if lab[m] is None else _div(rev[m], lab[m][0] / FTE_HOURS_PER_MONTH)))


def avg_time_to_list(ctx: _Ctx, start: date, end: date) -> dict:
    """Average time to list (days): avg(first listed − manifested_at) for items first listed in the month."""
    fl = _b_first_listings(ctx, start, end)
    return {m: (None if v is None else v["days_manifest"]) for m, v in fl.items()}


def items_identified(ctx: _Ctx, start: date, end: date) -> dict:
    """Items identified for e-commerce: items flagged at a store (AI or person) in the month. No channel."""
    return {m: _f(v) for m, v in _b_items_flow(ctx, start, end, "identified_at_utc").items()}


def items_sent(ctx: _Ctx, start: date, end: date) -> dict:
    """Items sent to e-commerce: items manifested in the month. No channel."""
    return {m: _f(v) for m, v in _b_items_flow(ctx, start, end, "manifested_at_utc").items()}


# --- Inventory ------------------------------------------------------------------------------------
def sell_through(ctx: _Ctx, start: date, end: date) -> dict:
    """★ Sell-through rate (listing-month cohort): items whose first listing started in month m and that sold
    (any channel) within 30 days of that first listing ÷ items first listed in m. Cohorts whose 30-day window
    is not yet closed are provisional (the card says so)."""
    fl = _b_first_listings(ctx, start, end)
    return {m: (None if v is None else _div(v["sold30"], v["n"])) for m, v in fl.items()}


def days_donation_to_listing(ctx: _Ctx, start: date, end: date) -> dict:
    """Days from donation to listing: avg(first listed − identified_at) for items first listed in the month.
    (identified_at = flagged at the store, our proxy for donation/processing date.)"""
    fl = _b_first_listings(ctx, start, end)
    return {m: (None if v is None else v["days_ident"]) for m, v in fl.items()}


def unlisted_backlog(ctx: _Ctx, start: date, end: date) -> dict:
    """Unlisted inventory backlog: items manifested to e-com by month end and not yet first-listed at month end.
    Unlisted items have no channel (None under a channel filter)."""
    return {m: _f(v) for m, v in _b_backlog(ctx, start, end).items()}


def unsold_pct(ctx: _Ctx, start: date, end: date) -> dict:
    """Unsold inventory %: listings that ended unsold in the month ÷ listings that ended (sold or unsold)."""
    e = _b_listing_ends(ctx, start, end)
    return {m: (None if v is None else _div(v[1], v[0])) for m, v in e.items()}


def days_to_sell(ctx: _Ctx, start: date, end: date) -> dict:
    """Days to sell: avg(first sale paid_at − first listed) for items whose first sale is in the month."""
    return _b_sales_timing(ctx, start, end)


def relisted_pct(ctx: _Ctx, start: date, end: date) -> dict:
    """Relisted inventory %: distinct items with a relist (relist_of not null) started in the month ÷ distinct
    items with any listing started in the month."""
    s = _b_listing_starts(ctx, start, end)
    return {m: (None if v is None else _div(v[1], v[0])) for m, v in s.items()}


def avg_selling_price(ctx: _Ctx, start: date, end: date) -> dict:
    """Average selling price: Σ line sale_price ÷ Σ units sold (fct_order_lines)."""
    li = _b_lines(ctx, start, end)
    return {m: (None if v is None else _div(v[0], v[1])) for m, v in li.items()}


def median_sale_price(ctx: _Ctx, start: date, end: date) -> dict:
    """Median sale price: median unit price over order lines in the month."""
    li = _b_lines(ctx, start, end)
    return {m: (None if v is None else v[2]) for m, v in li.items()}


def top_categories_by_revenue(ctx: _Ctx, start: date, end: date) -> dict:
    """Top 10 categories by revenue: categories ranked by Σ line sale_price. Value = revenue of the #1 category;
    the card carries the ranked top-10 list in ``breakdown``."""
    def cat_rev(c, s, e):
        li = _b_lines(c, s, e)
        return {m: (None if v is None else round(v[0], 2)) for m, v in li.items()}
    return _top_category(cat_rev, ctx, start, end)


# --- Engagement -----------------------------------------------------------------------------------
def buyers(ctx: _Ctx, start: date, end: date) -> dict:
    """Number of buyers: distinct buyer_key with an order in the month (buyer_key is channel-scoped)."""
    return {m: (None if v is None else float(v[0])) for m, v in _b_buyers(ctx, start, end).items()}


def new_buyers(ctx: _Ctx, start: date, end: date) -> dict:
    """New buyers: buyers whose first-ever order (in the harmonized history) is in the month."""
    return {m: (None if v is None else float(v[2])) for m, v in _b_buyers(ctx, start, end).items()}


def repeat_buyer_rate(ctx: _Ctx, start: date, end: date) -> dict:
    """Repeat buyer rate: buyers in the month with any order before the 1st of the month ÷ buyers in the month.
    Note: history starts 2025-09-01, so early months understate repeats."""
    return {m: (None if v is None else _div(v[1], v[0])) for m, v in _b_buyers(ctx, start, end).items()}


def refund_rate(ctx: _Ctx, start: date, end: date) -> dict:
    """Refund rate: refunds $ (by refund date) ÷ revenue."""
    r, rev = _b_refunds(ctx, start, end), total_revenue(ctx, start, end)
    return _map(_months(start, end), lambda m: _div(r[m], rev[m]))


def _not_available(ctx: _Ctx, start: date, end: date) -> dict:
    """No source yet: value is always None."""
    return {m: None for m in _months(start, end)}


def customer_satisfaction(ctx, start, end):
    """Customer satisfaction rating (marketplace seller rating). No feedback source yet -> None."""
    return _not_available(ctx, start, end)


def net_promoter_score(ctx, start, end):
    """Net Promoter Score. No survey source -> None."""
    return _not_available(ctx, start, end)


def marketplace_conversion(ctx, start, end):
    """Marketplace conversion (sold ÷ viewed or bid). No views/bids data -> None."""
    return _not_available(ctx, start, end)


# ========================================================================================= registry
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
    fn: Callable = field(repr=False)
    status_mode: str = "yoy"             # yoy | target | sign | none
    has_breakdown: bool = False


def _k(id, label, pillar, unit, better, availability, source, fn, *, status_mode="yoy", breakdown=False,
       formula=None) -> KpiDef:
    doc = " ".join((fn.__doc__ or "").split())
    return KpiDef(id=id, label=label, pillar=pillar, unit=unit, anchor=id in ANCHORS, scorecard=id in SCORECARD,
                  better=better, availability=availability, source=source, formula=formula or doc, fn=fn,
                  status_mode=status_mode, has_breakdown=breakdown)


_O, _L, _R = "fct_orders", "fct_order_lines", "fct_refunds"
_KPI_LIST = [
    # growth
    _k("total_revenue", "Total e-com revenue", "growth", "usd", "up", "built", f"{_O}; {_L} (store)", total_revenue),
    _k("revenue_growth_yoy", "Revenue growth YoY", "growth", "pct", "up", "built", f"{_O}; {_L} (store)",
       revenue_growth_yoy, status_mode="sign"),
    _k("budget_attainment", "Budget vs actual", "growth", "pct", "up", "input", f"{_O}, fct_budget",
       budget_attainment, status_mode="budget"),
    _k("ecom_share_of_retail", "E-com share of donated-goods retail", "growth", "pct", "up", "input",
       f"{_O}, fct_monthly_inputs", ecom_share_of_retail),
    # profitability
    _k("net_margin", "E-com net margin", "profitability", "pct", "up", "input",
       f"{_O}/{_L}, {_R}, fct_fees, fct_labor, fct_monthly_inputs", net_margin),
    _k("gross_margin", "Gross margin", "profitability", "pct", "up", "built", f"{_O}/{_L}, {_R}, fct_fees",
       gross_margin),
    _k("profit_per_labor_hour", "Profit per labor hour", "profitability", "usd_per_hour", "up", "built",
       f"{_O}/{_L}, {_R}, fct_fees, fct_labor", profit_per_labor_hour),
    _k("top_categories_by_margin", "Top 10 categories by margin", "profitability", "usd", "up", "built",
       f"{_L}, {_R}, fct_fees", top_categories_by_margin, breakdown=True),
    # productivity
    _k("revenue_per_labor_hour", "Revenue per labor hour", "productivity", "usd_per_hour", "up", "built",
       f"{_O}/{_L}, fct_labor (+fct_listings for allocation)", revenue_per_labor_hour),
    _k("listings_created", "Listings created", "productivity", "count", "up", "built", "fct_listings",
       listings_created),
    _k("listings_per_day", "Listings created per day", "productivity", "count", "up", "built",
       "fct_listings, fct_labor", listings_per_day),
    _k("listings_per_employee", "Listings per employee", "productivity", "count", "up", "built",
       "fct_listings, fct_labor (role=lister)", listings_per_employee),
    _k("sales_per_employee", "Sales per employee", "productivity", "usd", "up", "built", f"{_O}/{_L}, fct_labor",
       sales_per_employee),
    _k("avg_time_to_list", "Average time to list", "productivity", "days", "down", "built",
       "fct_listings, fct_items", avg_time_to_list),
    _k("items_identified", "Items identified for e-com", "productivity", "count", "up", "built", "fct_items",
       items_identified),
    _k("items_sent", "Items sent to e-com", "productivity", "count", "up", "built", "fct_items", items_sent),
    # inventory
    _k("sell_through", "Sell-through rate (30-day, listing cohort)", "inventory", "pct", "up", "built",
       f"fct_listings, {_L}, {_O}", sell_through, status_mode="target"),
    _k("days_donation_to_listing", "Days from donation to listing", "inventory", "days", "down", "built",
       "fct_listings, fct_items", days_donation_to_listing),
    _k("unlisted_backlog", "Unlisted inventory backlog", "inventory", "count", "down", "built", "fct_items",
       unlisted_backlog),
    _k("unsold_pct", "Unsold inventory %", "inventory", "pct", "down", "built", "fct_listings", unsold_pct),
    _k("days_to_sell", "Days to sell", "inventory", "days", "down", "built", f"fct_listings, {_L}, {_O}",
       days_to_sell),
    _k("relisted_pct", "Relisted inventory %", "inventory", "pct", "down", "built", "fct_listings", relisted_pct),
    _k("avg_selling_price", "Average selling price", "inventory", "usd", "up", "built", _L, avg_selling_price),
    _k("median_sale_price", "Median sale price", "inventory", "usd", "up", "built", _L, median_sale_price),
    _k("top_categories_by_revenue", "Top 10 categories by revenue", "inventory", "usd", "up", "built", _L,
       top_categories_by_revenue, breakdown=True),
    # engagement
    _k("repeat_buyer_rate", "Repeat buyer rate", "engagement", "pct", "up", "built", f"{_O} (buyer_key)",
       repeat_buyer_rate),
    _k("buyers", "Buyers", "engagement", "count", "up", "built", f"{_O} (buyer_key)", buyers),
    _k("new_buyers", "New buyers", "engagement", "count", "up", "built", f"{_O} (buyer_key)", new_buyers),
    _k("refund_rate", "Refund rate", "engagement", "pct", "down", "built", f"{_R}, {_O}", refund_rate),
    _k("customer_satisfaction", "Customer satisfaction rating", "engagement", "count", "up", "n/a",
       "none (eBay/Amazon feedback not loaded)", customer_satisfaction, status_mode="none"),
    _k("net_promoter_score", "Net Promoter Score", "engagement", "count", "up", "n/a", "none (no survey)",
       net_promoter_score, status_mode="none"),
    _k("marketplace_conversion", "Marketplace conversion", "engagement", "pct", "up", "n/a",
       "none (no views/bids data)", marketplace_conversion, status_mode="none"),
]
KPIS: dict[str, KpiDef] = {k.id: k for k in _KPI_LIST}
assert set(ANCHORS) <= set(SCORECARD) and len(SCORECARD) == 15 and set(SCORECARD) <= set(KPIS)


# ========================================================================================= public API
def connect_harmonized(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Read-only connection to data/harmonized.duckdb (or ``path``)."""
    return duckdb.connect(str(path or HARMONIZED_PATH), read_only=True)


def _round(unit: str, v):
    if v is None:
        return None
    return round(float(v), 4 if unit == "pct" else 2)


def _range_values(ctx: _Ctx, kpi_id: str, start: date, end: date) -> dict:
    kd = KPIS[kpi_id]
    try:
        vals = kd.fn(ctx, start, end)
    except _NotAttributable:
        vals = {m: None for m in _months(start, end)}
    return {m: _round(kd.unit, vals.get(m)) for m in _months(start, end)}


def _ctx(con, store, channel, category=None, memo=None) -> _Ctx:
    return _Ctx(con, Filters(store or None, channel or None, category or None), memo)


def compute(con, kpi_id: str, month: date, store: str | None = None, channel: str | None = None,
            category: str | None = None) -> float | None:
    """Value of one KPI for the month containing ``month`` (None when data is missing / not attributable)."""
    m = month_start(month)
    return _range_values(_ctx(con, store, channel, category), kpi_id, m, m)[m]


def default_month(con) -> date | None:
    """Latest month with orders in the harmonized model."""
    d = con.execute("SELECT max(business_date) FROM fct_orders").fetchone()[0]
    return month_start(d) if d else None


def series(con, kpi_id: str, months: int = 13, store: str | None = None, channel: str | None = None,
           end: date | None = None, category: str | None = None) -> list[dict]:
    """``months`` monthly points ending at ``end`` (default: latest month with orders), oldest first:
    [{"month": "YYYY-MM-01", "value": float|None}]."""
    end = month_start(end) if end else default_month(con)
    if end is None:
        return []
    start = add_months(end, -(months - 1))
    vals = _range_values(_ctx(con, store, channel, category), kpi_id, start, end)
    return [{"month": m.isoformat(), "value": vals[m]} for m in _months(start, end)]


def _status(kd: KpiDef, value, prior_year) -> str | None:
    if value is None or kd.status_mode == "none":
        return None
    if kd.status_mode == "target":            # sell-through vs the 50-55 % store target
        lo = SELL_THROUGH_TARGET[0]
        return "good" if value >= lo else ("watch" if value >= lo - 0.05 else "bad")
    if kd.status_mode == "sign":              # growth: positive good, small decline watch
        return "good" if value >= 0 else ("watch" if value > -WATCH_BAND_REL else "bad")
    if kd.status_mode == "budget":            # attainment: >=100 % good, >=95 % watch
        return "good" if value >= 1 else ("watch" if value >= 1 - WATCH_BAND_REL else "bad")
    if prior_year is None:
        return None
    sign = 1 if kd.better == "up" else -1
    if kd.unit == "pct":
        delta, band = (value - prior_year) * sign, WATCH_BAND_PCT_POINTS
    else:
        if prior_year == 0:
            return None
        delta, band = (value - prior_year) / abs(prior_year) * sign, WATCH_BAND_REL
    return "good" if delta >= 0 else ("watch" if delta > -band else "bad")


def breakdown(con, kpi_id: str, month: date, by: str, store: str | None = None, channel: str | None = None,
              category: str | None = None, top: int | None = None, _memo: dict | None = None) -> list[dict]:
    """KPI value per ``by`` in {'category','channel','store'} for one month, sorted descending:
    [{"key": "Jewelry", "value": 1234.5}]. Values that are None are dropped."""
    m = month_start(month)
    if by == "category":
        keys = _categories(_ctx(con, None, None, category, _memo))
    elif by == "channel":
        keys = [r[0] for r in con.execute("SELECT DISTINCT channel FROM fct_orders ORDER BY 1").fetchall()]
    elif by == "store":
        keys = [r[0] for r in con.execute("SELECT store_id FROM dim_store ORDER BY 1").fetchall()]
    else:
        raise ValueError(f"unknown breakdown dimension {by!r}")
    out = []
    for key in keys:
        kw = {"store": store, "channel": channel, "category": category}
        kw[by] = key
        ctx = _ctx(con, kw["store"], kw["channel"], kw["category"], _memo)
        if kpi_id == "top_categories_by_margin":
            v = _round("usd", gross_profit(ctx, m, m)[m])
        elif kpi_id == "top_categories_by_revenue":
            li = _b_lines(ctx, m, m)[m]
            v = _round("usd", li[0] if li else None)
        else:
            v = _range_values(ctx, kpi_id, m, m)[m]
        if v is not None:
            out.append({"key": key, "value": v})
    out.sort(key=lambda r: r["value"], reverse=True)
    return out[:top] if top else out


def kpi_card(con, kpi_id: str, month: date, store: str | None = None, channel: str | None = None,
             category: str | None = None, _memo: dict | None = None) -> dict:
    """Card for one KPI: value, prior month, prior year, 13-month trend, status and provenance.
    Extra keys beyond the contract: ``provisional`` (bool), ``notes`` (list[str]), ``as_of`` (ISO date),
    ``breakdown`` (top-10 list, only for top_categories_* KPIs), ``scorecard_row``."""
    kd = KPIS[kpi_id]
    m = month_start(month)
    start = add_months(m, -12)
    ctx = _ctx(con, store, channel, category, _memo)
    vals = _range_values(ctx, kpi_id, start, m)
    value, py, pm = vals[m], vals[start], vals[add_months(m, -1)]
    notes: list[str] = []
    as_of = ctx.as_of()
    provisional = bool(as_of and as_of < add_months(m, 1) - _one_day())
    if provisional:
        notes.append(f"Month in progress: data through {as_of.isoformat()}.")
    if kpi_id == "sell_through" and as_of is not None:
        from datetime import timedelta
        if as_of < add_months(m, 1) - _one_day() + timedelta(days=SELL_THROUGH_WINDOW_DAYS):
            provisional = True
            notes.append("Cohort's 30-day sell window not closed yet; value will rise as late sales arrive.")
    if kpi_id in ("net_margin", "gross_margin", "profit_per_labor_hour", "top_categories_by_margin") \
            and not _shipping_labels_available(ctx):
        notes.append("Shipping label cost not in the harmonized model; excluded from costs.")
    if ctx.f.any and kpi_id in ("net_margin", "revenue_per_labor_hour", "listings_per_employee",
                                "sales_per_employee", "profit_per_labor_hour"):
        notes.append("Labor allocated by share of listings created; overhead by share of revenue.")
    if kd.availability == "n/a":
        notes.append("Not yet available: no source data.")
    card = {
        "id": kd.id, "label": kd.label, "pillar": kd.pillar, "unit": kd.unit, "better": kd.better,
        "anchor": kd.anchor, "scorecard": kd.scorecard,
        "scorecard_row": SCORECARD_ROWS[SCORECARD.index(kd.id) // 3] if kd.scorecard else None,
        "value": value, "prior_month": pm, "prior_year": py,
        "trend": [{"month": mm.isoformat(), "value": vals[mm]} for mm in _months(start, m)],
        "status": _status(kd, value, py),
        "availability": kd.availability, "source": kd.source, "formula": kd.formula,
        "provisional": provisional, "notes": notes, "as_of": as_of.isoformat() if as_of else None,
    }
    if kd.has_breakdown:
        card["breakdown"] = breakdown(con, kpi_id, m, "category", store, channel, category, top=10, _memo=_memo)
    return card


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def scorecard(con, month: date, store: str | None = None, channel: str | None = None,
              category: str | None = None) -> dict:
    """{anchors: [3 cards], scorecard: [15 cards, slide-35 order], pillars: {pillar: [cards of every KPI]},
    month, as_of}. Cards are computed once and shared between the three views."""
    memo: dict = {}
    cards = {k: kpi_card(con, k, month, store, channel, category, _memo=memo) for k in KPIS}
    as_of = next(iter(cards.values()))["as_of"] if cards else None
    return {
        "month": month_start(month).isoformat(), "as_of": as_of,
        "filters": {"store": store, "channel": channel, "category": category},
        "anchors": [cards[k] for k in ANCHORS],
        "scorecard": [cards[k] for k in SCORECARD],
        "pillars": {p: [c for c in cards.values() if c["pillar"] == p] for p in PILLARS},
    }
