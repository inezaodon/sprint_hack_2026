"""Metric catalog v1 (contract B in the sprint plan): one entry per metric.

The intelligence engine reads this to turn a question into a ChartSpec (it may only name metric ids, breakdowns and
filters listed here); the dashboard reads it for labels, units and "show the math". The formulas themselves live in
`engine.py`, one plain function per metric. The AI never computes a number.

"Sales by platform" is `total_sales` broken down by `channel`; "items by department" is `items_sold` broken down by
`department`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SALES_BREAKDOWNS = ("channel", "department", "store", "day", "week", "month")
BOOKS_BREAKDOWNS = ("store", "week", "month")
SALES_FILTERS = ("channel", "department", "store")
BOOKS_FILTERS = ("store",)
ROLES = ("leadership", "dm", "store_manager")   # starting views: C-suite, Amanda/DMs, store managers


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    formula: str              # plain-English formula shown in "show the math"
    unit: str                 # usd | count
    table: str                # sales_line | books
    breakdowns: tuple         # allowed breakdowns
    filters: tuple            # allowed filters
    additive_over: tuple      # breakdowns whose parts must add up to the whole (checked on every result)
    roles: tuple              # starting views that show this metric

    def to_json(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}


_ALL = ROLES
_TIME = ("day", "week", "month")

CATALOG: dict[str, Metric] = {m.id: m for m in [
    Metric("total_sales", "Total sales", "Σ subtotal of paid items, refunds netted (shipping excluded)", "usd",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, SALES_BREAKDOWNS, _ALL),
    Metric("gross_sales", "Item sales (before refunds)",
           "Σ subtotal of paid items, refunds not subtracted (Goodwill's daily report basis)", "usd",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, SALES_BREAKDOWNS, ("leadership", "dm")),
    Metric("refunds", "Refunds", "Σ refunds issued, counted on the refund date", "usd",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, SALES_BREAKDOWNS, ("leadership", "dm")),
    Metric("orders", "Orders", "count of distinct paid order IDs", "count",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, ("channel",) + _TIME, ("leadership", "dm")),
    Metric("average_sale", "Average sale", "total sales ÷ orders", "usd",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, (), _ALL),
    Metric("shipping_charged", "Shipping charged", "Σ shipping charged to buyers, refunds netted", "usd",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, SALES_BREAKDOWNS, ("leadership", "dm")),
    Metric("items_sold", "Items sold", "Σ items on paid orders, refunds netted", "count",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, SALES_BREAKDOWNS, ("leadership", "dm")),
    Metric("customers", "Customers", "count of distinct buyers per platform (paid orders)", "count",
           "sales_line", SALES_BREAKDOWNS, SALES_FILTERS, ("channel",), ("leadership", "store_manager")),
    Metric("books_sold", "Books sold", "Σ books sold (Cash Monkey, weekly)", "count",
           "books", BOOKS_BREAKDOWNS, BOOKS_FILTERS, BOOKS_BREAKDOWNS, ("leadership", "dm")),
    Metric("books_revenue", "Book sales", "Σ book revenue (Cash Monkey, weekly)", "usd",
           "books", BOOKS_BREAKDOWNS, BOOKS_FILTERS, BOOKS_BREAKDOWNS, ("leadership", "dm")),
    Metric("books_scanned", "Books scanned", "Σ books scanned into Cash Monkey per store per week", "count",
           "books", BOOKS_BREAKDOWNS, BOOKS_FILTERS, BOOKS_BREAKDOWNS, ("leadership", "dm")),
]}

# Asked about but not ingested: answered with "not connected yet", never with a number.
NOT_CONNECTED: dict[str, tuple[str, str]] = {
    "labor_hours": ("Labor hours", "Thriftly and payroll"),
    "pieces_per_hour": ("Pieces per hour", "Thriftly"),
    "payroll": ("Payroll", "payroll"),
    "store_sales": ("Store / register sales", "Supro (already automated)"),
}

# Said out loud in the demo and listed on the limits slide.
KNOWN_LIMITS = [
    "Customers can't be matched across platforms: the same person on eBay and ShopGoodwill counts as two.",
    "Books are weekly (Cash Monkey export), so book metrics can't be broken down by day; "
    "a week belongs to the month its Monday falls in.",
    "Refunds count on the day they are issued, not the day of the original sale.",
    "Labor, payroll and pieces per hour live in Thriftly and payroll and are not connected yet.",
    "Synthetic data on real Upright headers; no real Goodwill data.",
]


def catalog_json() -> dict:
    """The catalog as JSON for the intelligence engine prompt and the dashboard."""
    return {
        "metrics": [m.to_json() for m in CATALOG.values()],
        "not_connected": {k: {"label": l, "lives_in": s} for k, (l, s) in NOT_CONNECTED.items()},
        "periods": ["today", "yesterday", "this_week", "last_week", "this_month", "last_month",
                    "last_7_days", "last_30_days", "last_<N>_weeks", "all", {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}],
        "known_limits": KNOWN_LIMITS,
    }
