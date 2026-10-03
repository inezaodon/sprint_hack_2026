"""Export the "how did we get these numbers" audit docs (AGENTS.md contract 5) for the artifact.

    .venv/bin/python artifact/export_lineage.py [out_dir]        # default: artifact/data

Writes lineage__overview.json, lineage__metrics.json, lineage__ledger.json, lineage__drill.json.

Everything here is deterministic code over read-only DuckDB files: no model is involved and no number is typed in by
hand. The ledger recomputes each headline number several independent ways (native source DBs, harmonized tables, the
generator's truth world, the Daily Pulse warehouse, the JSON docs the page reads) and reports match/diff honestly.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from goodwill_pulse import kpi as kpi_mod  # noqa: E402
from goodwill_pulse.config import DATA_DIR  # noqa: E402

TZ = "America/New_York"
ART_DATA = HERE / "data"
SOURCES_DIR = DATA_DIR / "sources"
HARMONIZED = DATA_DIR / "harmonized.duckdb"
WAREHOUSE = DATA_DIR / "warehouse.duckdb"
EXPORT_SCRIPT = ROOT.parent / "tools" / "export_artifact_docs.py"
DOC_LIMIT = 250_000
CHANNELS = ["shopgoodwill", "ebay", "amazon", "goodwillfinds", "goodwillbooks"]
MARKETPLACE_DBS = ["amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks"]
MONEY_TOL = 0.005
DRILL_DAYS = 7
DRILL_CAP = 60


# ------------------------------------------------------------------------------------------------ helpers
def one_line(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def dumps(obj) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"), ensure_ascii=False)


def num(x):
    if x is None:
        return None
    return round(float(x), 2)


def lab_connection() -> duckdb.DuckDBPyConnection:
    """One in-memory connection with every DB attached READ_ONLY under a stable alias."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    for n in MARKETPLACE_DBS:
        con.execute(f"ATTACH '{SOURCES_DIR / (n + '.duckdb')}' AS {n} (READ_ONLY)")
    con.execute(f"ATTACH '{SOURCES_DIR / '_truth.duckdb'}' AS truth (READ_ONLY)")
    con.execute(f"ATTACH '{HARMONIZED}' AS harmonized (READ_ONLY)")
    if WAREHOUSE.exists():
        try:
            con.execute(f"ATTACH '{WAREHOUSE}' AS warehouse (READ_ONLY)")
        except duckdb.IOException:
            # the API server holds a write lock on warehouse.duckdb: read a snapshot copy (file + WAL) instead
            tmp = DATA_DIR / "_tmp" / "lineage"
            tmp.mkdir(parents=True, exist_ok=True)
            for suffix in ("", ".wal"):
                src = Path(str(WAREHOUSE) + suffix)
                if src.exists():
                    shutil.copyfile(src, tmp / ("warehouse.duckdb" + suffix))
            con.execute(f"ATTACH '{tmp / 'warehouse.duckdb'}' AS warehouse (READ_ONLY)")
    return con


def read_doc(name: str):
    return json.loads((ART_DATA / name).read_text())


def put(out: Path, doc_id: str, obj) -> int:
    s = dumps(obj)
    size = len(s.encode())
    if size >= DOC_LIMIT:
        raise SystemExit(f"lineage/{doc_id}: {size:,} bytes exceeds {DOC_LIMIT:,}")
    (out / f"lineage__{doc_id}.json").write_text(s)
    print(f"lineage/{doc_id}: {size:,} bytes")
    return size


# ----------------------------------------------------------------------------------- table descriptions
# (grain, description) per "db.table". Written from sql/sources/*.sql, sql/harmonized/schema.sql and docs/CONTRACT.md.
T = {
    # truth world
    "_truth.stores": ("one row per Goodwill store", "24 donor stores with AI flagging / Ecom Eye flags."),
    "_truth.employees": ("one row per e-com employee", "Lister, photographer, shipper and manager roles with hourly rate."),
    "_truth.items": ("one row per physical item (SKU)", "Item lifecycle in the generated world: identified, manifested, listed, sold."),
    "_truth.listings": ("one row per listing of an item on a channel", "Listings and relists across the five channels, with tool (Upright or Cash Monkey)."),
    "_truth.buyers": ("one row per buyer per channel", "Pseudo buyers with the channel's native reference."),
    "_truth.orders": ("one row per order", "The clean generated orders every marketplace DB is derived from."),
    "_truth.order_lines": ("one row per order line", "Items in each truth order with price and allocated fee."),
    "_truth.refunds": ("one row per refund", "About 2.5 percent of orders get a refund; some cross midnight or month end."),
    "_truth.payouts": ("one row per marketplace payout", "Gross, fees, refunds and net per channel payout period."),
    "_truth.shipping_charges": ("one row per carrier charge", "Postage charged per order (FedEx, OSM, Pitney Bowes, EasyPost)."),
    "_truth.labor": ("one row per employee per work date", "Timeclock hours."),
    "_truth.productivity": ("one row per employee per work date", "Accepted, rejected, photographed and posted counts."),
    "_truth.budget": ("one row per month per channel", "Revenue budget (prior-year actual x 1.08 plus noise)."),
    "_truth.monthly_inputs": ("one row per month", "Overhead allocation and store retail revenue entered monthly."),
    # amazon
    "amazon._dirty_data": ("one row per planted dirty record", "Documented dirty rows (duplicate API row, lowercase SKU, missing store code, test order)."),
    "amazon.orders": ("one row per Amazon order", "Seller Central order header; purchase_date is ISO text in UTC; buyer_email TEST marks a test order."),
    "amazon.order_items": ("one row per order item", "Item price, shipping and tax per SKU; the store is only knowable from seller_sku."),
    "amazon.financial_events": ("one row per transaction-report event", "Order, Refund and Service Fee events; times are Pacific text, fees are negative."),
    "amazon.settlements": ("one row per 14-day settlement", "Settlement period, deposit date and total deposited."),
    "amazon.listings": ("one row per offer", "Active, sold and inactive book offers keyed by seller_sku."),
    # ebay
    "ebay._dirty_data": ("one row per planted dirty record", "Documented dirty rows (duplicate transaction, lowercase SKU, missing store code, test order)."),
    "ebay.orders": ("one row per eBay order", "Fulfillment API order; money values are text; creationDate is ISO UTC; one seller account for both tools."),
    "ebay.line_items": ("one row per order line item", "SKU, quantity and line cost as text; the SKU prefix tells general merch from books."),
    "ebay.transactions": ("one row per Finances API transaction", "SALE, REFUND, NON_SALE_CHARGE and SHIPPING_LABEL rows with fees and payout id."),
    "ebay.payouts": ("one row per daily payout", "Payout date and amount (text) deposited to the bank."),
    "ebay.listings": ("one row per listing", "Listing start/end and status; a relist has a new legacyItemId pointing at its parent."),
    # shopgoodwill
    "shopgoodwill._dirty_data": ("one row per planted dirty record", "Documented dirty rows (duplicate fee, lowercase code, test listing and purchase)."),
    "shopgoodwill.auctions": ("one row per auction listing", "Auction start/end, bids and status; times are naive Pacific."),
    "shopgoodwill.sales": ("one row per sold item", "Hammer price, shipping, handling, tax and refund; an order with several items repeats the OrderID."),
    "shopgoodwill.seller_fees": ("one row per fee", "Commission (per item) and PaymentProcessing (per order) amounts."),
    "shopgoodwill.periodic_statements": ("one row per statement period", "Three statements a month (days 1-10, 11-20, 21-end) with gross, fees, refunds and net remit."),
    # goodwillfinds
    "goodwillfinds._dirty_data": ("one row per planted dirty record", "Documented dirty rows (vendor typo, duplicate line, test order)."),
    "goodwillfinds.orders": ("one row per order", "Shopify-style order; created_at is ISO text with an Eastern offset; name like #GF10421."),
    "goodwillfinds.line_items": ("one row per order line", "SKU, vendor (the store) and unit price."),
    "goodwillfinds.refunds": ("one row per refund", "Refund amount and time per order."),
    "goodwillfinds.payouts": ("one row per weekly payout", "Charges gross, fees, refunds and amount paid."),
    "goodwillfinds.products": ("one row per product", "Published products with status and price."),
    # goodwillbooks
    "goodwillbooks._dirty_data": ("one row per planted dirty record", "Documented dirty rows (missing store code, lowercase SKU, duplicate line, test order)."),
    "goodwillbooks.sales_orders": ("one row per order", "Money in integer cents; order_date is Eastern wall-clock text 'MM/DD/YYYY HH:MM'; refund on the header."),
    "goodwillbooks.sales_order_lines": ("one row per order line", "SKU, store_code, quantity and unit price in cents."),
    "goodwillbooks.monthly_statements": ("one row per month", "Gross, fees, refunds and net in cents, paid the next month."),
    "goodwillbooks.inventory": ("one row per book", "Listed and delisted dates, status and price in cents."),
    # ops
    "ops.stores": ("one row per store", "Store dimension from the Upright back office."),
    "ops.upright_items": ("one row per item sent to e-commerce", "Identified, manifested and posted timestamps; canonical store and category."),
    "ops.operational_productivity": ("one row per employee per work date", "Upright operational productivity report."),
    "ops.employees": ("one row per employee", "Role and hourly rate."),
    "ops.timeclock": ("one row per employee per work date", "Hours worked."),
    "ops.budget": ("one row per month per channel", "Revenue budget."),
    "ops.monthly_inputs": ("one row per month", "Manual monthly inputs: overhead allocation and store retail revenue."),
    # finance
    "finance._dirty_data": ("one row per planted dirty record", "Documented dirty rows (cross-month FedEx refund, unmatched jewelry SKU)."),
    "finance.bank_0101": ("one row per bank line", "Bank account 0101 postings used by the month-end close."),
    "finance.fedex_invoices": ("one row per invoice line", "FedEx shipment charges and credits."),
    "finance.gwb_statement": ("one row per statement line", "Goodwillbooks monthly statement detail."),
    "finance.gwb_statement_header": ("one row per month", "Goodwillbooks statement totals."),
    "finance.jewelry_report": ("one row per jewelry sale", "Jewelry supplier report used for the jewelry journal."),
    # harmonized
    "harmonized.dim_store": ("one row per store", "Canonical store dimension (Store01..Store24)."),
    "harmonized.dim_channel": ("one row per channel", "Five channels with label, Daily Pulse row and listing tool."),
    "harmonized.dim_date": ("one row per calendar date", "Date dimension 2025-2028 with month start, ISO week and weekday."),
    "harmonized.fct_orders": ("one row per kept order (test, canceled and duplicate orders removed)", "Canonical order header; business_date is the America/New_York date of paid_at_utc; refund_amount is attributed to the order's own date."),
    "harmonized.fct_order_lines": ("one row per order line", "Lines with store, category and line of business resolved once; fee_alloc is the order fee spread over lines."),
    "harmonized.fct_refunds": ("one row per refund", "Dated by when the money went back, not the order date."),
    "harmonized.fct_fees": ("one row per fee component", "Referral, final value, commission, payment, shipping_label and other fees; positive means cost."),
    "harmonized.fct_payouts": ("one row per marketplace payout", "Gross, fees, refunds and net as the bank sees it."),
    "harmonized.fct_listings": ("one row per listing", "Listings with canonical store and category; relist_of links relists."),
    "harmonized.fct_items": ("one row per item", "Item lifecycle: identified, manifested, first listed, first sold."),
    "harmonized.fct_labor": ("one row per employee per work date", "Hours and labor cost."),
    "harmonized.fct_budget": ("one row per month per channel", "Revenue budget."),
    "harmonized.fct_monthly_inputs": ("one row per month", "Overhead allocation and store retail revenue."),
    "harmonized.harmonize_runs": ("one row per harmonize build", "Source and output row counts and the excluded.* counts for each build."),
    "harmonized.dq_results": ("one row per quality check", "Result of each sql/checks file: severity, pass/fail and failing row count."),
}

DOC_WHAT = [
    (r"^site__options", "Site options: months, stores, channels, KPI catalog and data-through date."),
    (r"^month__", "KPI cards, per-store revenue and category breakdowns for one month."),
    (r"^series__all", "13-month trend by channel for every KPI card."),
    (r"^pulse__all", "Daily Pulse snapshots (report-file basis) for the latest 21 business dates."),
    (r"^daily__all", "Day x marketplace totals: orders, item sales, shipping, fees, refunds (from fct_orders)."),
    (r"^storecat__all", "Month x store x category pipeline: identified, sent, listed, sold units and revenue."),
    (r"^close__", "Month-end close run: sources, control totals, journal, invoices, comparison and exceptions."),
    (r"^quality__latest", "Latest data-quality check results."),
    (r"^lineage__overview", "This audit view: tables, pipeline steps, exclusions and builds."),
    (r"^lineage__metrics", "Concept map: every measure and KPI with synonyms, formula and SQL."),
    (r"^lineage__ledger", "Independent recomputation of headline numbers with match/diff status."),
    (r"^lineage__drill", "Order rows behind each day x channel total for the last 7 business days."),
]


def doc_what(fname: str) -> str:
    for pat, w in DOC_WHAT:
        if re.search(pat, fname):
            return w
    return "Artifact data document."


# ------------------------------------------------------------------------------------------------- overview
def describe_db(path: Path, alias: str) -> list[dict]:
    con = duckdb.connect(str(path), read_only=True)
    out = []
    try:
        for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' "
                                "ORDER BY 1").fetchall():
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            cols = con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=? "
                               "ORDER BY ordinal_position", [t]).fetchall()
            grain, desc = T.get(f"{alias}.{t}", ("n/a", "No description written."))
            out.append({"db": alias, "table": t, "rows": n, "columns": [{"name": c, "type": ty} for c, ty in cols],
                        "grain": grain, "description": desc})
    finally:
        con.close()
    return out


def build_overview(sizes: dict[str, int]) -> dict:
    sources = []
    for p in sorted(SOURCES_DIR.glob("*.duckdb")):
        sources += describe_db(p, p.stem)
    harmonized = describe_db(HARMONIZED, "harmonized")
    h = duckdb.connect(str(HARMONIZED), read_only=True)
    run = h.execute("SELECT run_id, started_at, finished_at, source_row_counts, output_row_counts FROM harmonize_runs "
                    "ORDER BY started_at DESC LIMIT 1").fetchone()
    src_counts, out_counts = json.loads(run[3]), json.loads(run[4])
    dq = h.execute("SELECT check_id, severity, status, failing_rows, run_at, description FROM dq_results "
                   "ORDER BY check_id").fetchall()
    h.close()
    ex = {k.split(".", 1)[1]: v for k, v in src_counts.items() if k.startswith("excluded.")}
    run_at = max(r[4] for r in dq) if dq else None
    S = ["amazon", "ebay", "shopgoodwill", "goodwillfinds", "goodwillbooks", "ops", "finance"]
    pipeline = [
        {"id": "truth", "title": "Generated truth world",
         "detail": "goodwill_pulse/gen/truth.py builds one consistent synthetic world (2025-09-01 to 2026-10-03, seeded, "
                   "deterministic): stores, items, listings, orders, refunds, payouts, labor, budget. All data is synthetic.",
         "tables": [r["db"] + "." + r["table"] for r in sources if r["db"] == "_truth"]},
        {"id": "sources", "title": "Seven source DBs shaped like each marketplace's own export",
         "detail": "goodwill_pulse/sources/*.py write amazon, ebay, shopgoodwill, goodwillfinds, goodwillbooks, ops and "
                   "finance with the native quirks kept (text money on eBay, cents on Goodwillbooks, Pacific naive times on "
                   "ShopGoodwill, offsets on GoodwillFinds). About 0.1 percent of rows are deliberately dirty and listed in "
                   "each DB's _dirty_data table.",
         "tables": [f"{d}.{t}" for d in S for t in sorted({r['table'] for r in sources if r['db'] == d})]},
        {"id": "staging", "title": "Harmonize SQL staging",
         "detail": "sql/harmonize/20-24 run per marketplace on a connection with the source DBs attached read-only. They "
                   "parse money and time zones, collapse duplicate API rows, flag test and canceled orders (exclude_reason) "
                   "and union into stg_orders_all. Staging tables are TEMP and never persisted.",
         "tables": ["stg_amazon_orders", "stg_ebay_orders", "stg_shopgoodwill_orders", "stg_goodwillfinds_orders",
                    "stg_goodwillbooks_orders", "stg_orders_all"]},
        {"id": "canonical", "title": "Canonical fct_ and dim_ tables (harmonized.duckdb)",
         "detail": "sql/harmonize/10, 30-40 build dim_ and fct_ tables. Kept orders are those with no exclude_reason and a "
                   "parseable paid time. Excluded on purpose in the latest build: "
                   f"{ex.get('test_orders', 0)} test orders, {ex.get('test_skus', 0)} test SKUs, "
                   f"{ex.get('canceled_orders', 0)} canceled orders, {ex.get('unparseable_paid_at', 0)} orders with an "
                   "unparseable paid time. business_date is the America/New_York date. harmonize_runs records the counts.",
         "tables": [r["table"] for r in harmonized if r["table"].startswith(("fct_", "dim_")) or r["table"] == "harmonize_runs"]},
        {"id": "quality", "title": "Quality checks",
         "detail": f"sql/checks/*.sql write one row per check to dq_results ({len(dq)} checks). They compare source to "
                   "output counts, order totals to lines, payouts to orders, and flag missing store ids and incomplete months.",
         "tables": ["dq_results"]},
        {"id": "aggregates", "title": "KPI and aggregate queries",
         "detail": "goodwill_pulse/kpi.py computes every KPI from the fct_ tables. tools/export_artifact_docs.py adds the daily "
                   "(day x marketplace) and storecat (month x store x category) aggregates.",
         "tables": ["fct_orders", "fct_order_lines", "fct_refunds", "fct_fees", "fct_labor", "fct_listings", "fct_items",
                    "fct_budget", "fct_monthly_inputs"]},
        {"id": "pulse", "title": "Daily Pulse from report files",
         "detail": "A second path: Upright and Cash Monkey report files are loaded into warehouse.duckdb and rolled up by "
                   "business date for the Pulse tab. The ledger compares it with the harmonized model.",
         "tables": ["warehouse.orders", "warehouse.order_lines", "warehouse.report_files"]},
        {"id": "docs", "title": "Artifact docs",
         "detail": "tools/export_artifact_docs.py and artifact/export_lineage.py write JSON docs into artifact/data. "
                   "The page reads only those docs.",
         "tables": []},
        {"id": "page", "title": "Page",
         "detail": "Goodwill E-Com Pulse is one HTML file. Numbers are always computed by code from stored data; a model "
                   "never produces a displayed number.",
         "tables": []},
    ]
    docs = []
    for f in sorted(ART_DATA.glob("*.json")):
        b = sizes.get(f.name, f.stat().st_size)
        docs.append({"path": f"artifact/data/{f.name}", "bytes": b, "what": doc_what(f.name)})
    for name in sizes:  # lineage docs written in this run
        if not any(d["path"].endswith(name) for d in docs):
            docs.append({"path": f"artifact/data/{name}", "bytes": sizes[name], "what": doc_what(name)})
    docs.sort(key=lambda d: d["path"])
    return {
        "generated_at": datetime.now(ZoneInfo(TZ)).isoformat(timespec="seconds"),
        "pipeline": pipeline,
        "sources": sources,
        "harmonized": harmonized,
        "artifact_docs": docs,
        "builds": {
            "harmonize": {"run_id": run[0], "started_at": str(run[1]), "seconds": round((run[2] - run[1]).total_seconds(), 2),
                          "source_rows": sum(v for k, v in src_counts.items() if not k.startswith("excluded.")),
                          "output_rows": sum(out_counts.values())},
            "quality": {"run_at": str(run_at), "total": len(dq), "passed": sum(1 for r in dq if r[2] == "pass"),
                        "checks": [{"check_id": r[0], "severity": r[1], "status": r[2], "failing_rows": r[3],
                                    "description": r[5]} for r in dq]},
        },
        "exclusions": {"test_orders": ex.get("test_orders", 0), "test_skus": ex.get("test_skus", 0),
                       "canceled_orders": ex.get("canceled_orders", 0), "unparseable_paid_at": ex.get("unparseable_paid_at", 0),
                       "note": "Counted in harmonize_runs.source_row_counts (excluded.*): dropped on purpose in staging, not lost."},
    }


# ------------------------------------------------------------------------------------------------- metrics
SYN = {
    "item_sales": ["revenue", "sales", "top line", "gross sales", "total sales", "item revenue", "merchandise sales", "subtotal", "hammer price"],
    "orders": ["order count", "number of orders", "transactions", "paid orders", "order volume", "customers"],
    "shipping": ["shipping charged", "postage", "shipping revenue", "delivery charges", "shipping collected"],
    "fees": ["marketplace fees", "commission", "selling fees", "platform fees", "final value fees", "referral fees", "seller fees"],
    "refunds": ["returns", "money back", "refunded amount", "refund dollars", "chargebacks"],
    "avg_order": ["AOV", "average order value", "average sale", "basket size", "order value", "average order"],
    "store_revenue": ["sales by store", "store sales", "revenue credited to store", "store revenue"],
    "units_sold": ["items sold", "units", "sold items", "pieces sold"],
    "items_identified": ["identified", "flagged items", "items flagged at the store", "items identified for e-com"],
    "items_sent": ["sent", "manifested", "manifested items", "sent to e-commerce", "shipped to e-com"],
    "items_listed": ["listed", "listings created", "first listed", "new listings"],
}
KPI_SYN = {
    "total_revenue": ["revenue", "sales", "top line", "gross sales", "total sales", "e-com revenue"],
    "revenue_growth_yoy": ["growth", "yoy growth", "year over year", "sales growth"],
    "budget_attainment": ["budget vs actual", "vs budget", "plan attainment"],
    "net_margin": ["profit margin", "net profit margin", "bottom line", "margin after labor"],
    "gross_margin": ["margin", "gross profit margin"],
    "refund_rate": ["returns rate", "return rate", "refund percent"],
    "sell_through": ["sell through", "sold within 30 days", "sell-through rate"],
    "avg_selling_price": ["ASP", "average price", "average sale price"],
    "median_sale_price": ["median price", "typical price"],
    "buyers": ["customers", "unique buyers"],
    "new_buyers": ["first-time buyers", "new customers"],
    "repeat_buyer_rate": ["repeat customers", "returning buyers", "repeat rate"],
    "unlisted_backlog": ["backlog", "inventory waiting to be listed", "unlisted inventory"],
    "revenue_per_labor_hour": ["revenue per hour", "RPLH", "labor productivity"],
    "listings_created": ["new listings", "listings posted"],
    "items_identified": ["identified items", "items flagged"],
    "items_sent": ["items manifested", "sent to e-com"],
}


def _script_text() -> str:
    try:
        return EXPORT_SCRIPT.read_text()
    except OSError:
        return ""


def _extract_daily_sql(script: str) -> tuple[str, bool]:
    m = re.search(r'daily = con\.execute\("""(.*?)"""\)', script, re.S)
    if m:
        return one_line(m.group(1)), True
    return one_line("""SELECT business_date, channel, count(*), round(sum(subtotal), 2),
        round(sum(coalesce(shipping_charged, 0)), 2), round(sum(coalesce(marketplace_fees, 0)), 2),
        round(sum(coalesce(refund_amount, 0)), 2) FROM fct_orders GROUP BY 1, 2 ORDER BY 1, 2"""), False


def _extract_storecat_sql(script: str, first_month: str) -> tuple[str, bool]:
    m = re.search(r'rows = con\.execute\(f"""(.*?)"""\)', script, re.S)
    if not m:
        return "", False
    s = m.group(1).replace("{TZ}", TZ).replace("{months[-1]}", first_month)
    return one_line(s), True


class _Recorder:
    """Wraps a DuckDB connection and records every statement a KPI function runs (with params inlined)."""

    def __init__(self, con):
        self.con, self.log = con, []

    def execute(self, sql, params=None):
        self.log.append((sql, list(params or [])))
        return self.con.execute(sql, params or [])


def _inline(sql: str, params: list) -> str:
    it = iter(params)

    def lit(v):
        if isinstance(v, date):
            return f"DATE '{v.isoformat()}'"
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        return str(v)
    return re.sub(r"\?", lambda _: lit(next(it)), sql)


def _kpi_md_formulas() -> dict[str, str]:
    out = {}
    p = ROOT / "docs" / "KPI_DEFINITIONS.md"
    for line in p.read_text().splitlines():
        m = re.match(r"^\| `([a-z_]+)` \| \w+ \| (.*?) \| (.*?) \| \w+ \|", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def build_metrics(latest_complete: date) -> dict:
    script = _script_text()
    daily_sql, daily_verified = _extract_daily_sql(script)
    opts = read_doc("site__options.json")
    first_month = opts["months"][-1]
    sc_sql, sc_verified = _extract_storecat_sql(script, first_month)
    ev_branch = {
        "items_identified": "SELECT strftime(timezone('{TZ}', identified_at_utc), '%Y-%m') m, store_id, category, 1 id, 0 se, 0 li, 0 su, 0.0 rv FROM fct_items WHERE identified_at_utc IS NOT NULL",
        "items_sent": "SELECT strftime(timezone('{TZ}', manifested_at_utc), '%Y-%m'), store_id, category, 0, 1, 0, 0, 0.0 FROM fct_items WHERE manifested_at_utc IS NOT NULL",
        "items_listed": "SELECT strftime(timezone('{TZ}', first_listed_at_utc), '%Y-%m'), store_id, category, 0, 0, 1, 0, 0.0 FROM fct_items WHERE first_listed_at_utc IS NOT NULL",
        "units_sold": "SELECT strftime(business_date, '%Y-%m'), store_id, category, 0, 0, 0, quantity, sale_price FROM fct_order_lines",
    }
    measures = []

    def daily(id, label, unit, formula, col, cols, notes):
        measures.append({"id": id, "label": label, "synonyms": SYN[id], "unit": unit, "kind": "daily",
                         "formula_text": formula, "sql": daily_sql,
                         "source_tables": ["fct_orders"], "columns_used": cols,
                         "notes": f"{notes} Column '{col}' of the statement; written to artifact/data/daily__all.json "
                                  f"(cols: date, channel, orders, item_sales, shipping, fees, refunds)."
                                  f" SQL taken verbatim from tools/export_artifact_docs.py: {daily_verified}."})
    daily("item_sales", "Item sales", "usd", "Sum of order subtotal (item price x quantity, before shipping, handling and tax) "
          "for the orders whose America/New_York business date is the day, per marketplace.", "item_sales (round(sum(subtotal), 2))",
          ["fct_orders.business_date", "fct_orders.channel", "fct_orders.subtotal"],
          "This is what the page calls revenue. Test and canceled orders are excluded upstream.")
    daily("orders", "Orders", "count", "Count of kept orders per business date and marketplace.", "orders (count(*))",
          ["fct_orders.business_date", "fct_orders.channel", "fct_orders.order_key"],
          "One row per order, not per item.")
    daily("shipping", "Shipping charged", "usd", "Sum of shipping charged to the buyer on those orders (handling is separate and not included).",
          "shipping (round(sum(coalesce(shipping_charged, 0)), 2))",
          ["fct_orders.business_date", "fct_orders.channel", "fct_orders.shipping_charged"],
          "Money the buyer paid for shipping, not what Goodwill paid the carrier. Handling is channel-dependent: ShopGoodwill reports Handling in its own column (fct_orders.handling, NOT in this measure), while eBay and GoodwillFinds send one delivery field that already includes handling (so it IS in this measure).")
    daily("fees", "Marketplace fees", "usd", "Sum of marketplace and payment fees on those orders, positive = cost. Shipping labels are not included.",
          "fees (round(sum(coalesce(marketplace_fees, 0)), 2))",
          ["fct_orders.business_date", "fct_orders.channel", "fct_orders.marketplace_fees"],
          "Fees sit on the order's own date, not the date the fee was posted.")
    daily("refunds", "Refunds", "usd", "Sum of refund dollars attributed to the order they belong to, so a refund lands on the ORDER's business date.",
          "refunds (round(sum(coalesce(refund_amount, 0)), 2))",
          ["fct_orders.business_date", "fct_orders.channel", "fct_orders.refund_amount"],
          "Differs from fct_refunds and the month-end close, which date a refund by when the money went back.")
    measures.append({"id": "avg_order", "label": "Average order", "synonyms": SYN["avg_order"], "unit": "usd", "kind": "daily",
                     "formula_text": "item_sales divided by orders over the selected days and marketplaces (ratio of sums, not an average of daily averages).",
                     "sql": daily_sql, "source_tables": ["fct_orders"],
                     "columns_used": ["fct_orders.subtotal", "fct_orders.order_key", "fct_orders.business_date", "fct_orders.channel"],
                     "notes": "Computed in the page from the daily doc: sum(item_sales) / sum(orders). No separate SQL; zero orders gives 0."})
    pm = [("store_revenue", "Store revenue", "usd", "Sum of order-line sale price credited to the store that sent the item (store resolved from SKU, vendor or store code), by month and category.",
           "SUM(sale_price) over fct_order_lines (the rv column)", ["fct_order_lines.business_date", "fct_order_lines.store_id", "fct_order_lines.category", "fct_order_lines.sale_price"], ["fct_order_lines"]),
          ("units_sold", "Units sold", "count", "Sum of line quantity by month, store and category (the su column).", "su", ["fct_order_lines.business_date", "fct_order_lines.store_id", "fct_order_lines.category", "fct_order_lines.quantity"], ["fct_order_lines"]),
          ("items_identified", "Items identified", "count", "Items flagged at a store, counted in the America/New_York month of identified_at_utc (the id column).", "id", ["fct_items.identified_at_utc", "fct_items.store_id", "fct_items.category"], ["fct_items"]),
          ("items_sent", "Items sent to e-com", "count", "Items manifested (sent to e-commerce), counted in the New York month of manifested_at_utc (the se column).", "se", ["fct_items.manifested_at_utc", "fct_items.store_id", "fct_items.category"], ["fct_items"]),
          ("items_listed", "Items listed", "count", "Items whose FIRST listing started in the New York month of first_listed_at_utc (the li column).", "li", ["fct_items.first_listed_at_utc", "fct_items.store_id", "fct_items.category"], ["fct_items"])]
    for id, label, unit, formula, col, cols, tabs in pm:
        measures.append({"id": id, "label": label, "synonyms": SYN[id], "unit": unit, "kind": "pipeline", "formula_text": formula,
                         "sql": sc_sql, "source_tables": tabs, "columns_used": cols,
                         "notes": f"Column '{col}' of the storecat statement in tools/export_artifact_docs.py (verbatim: {sc_verified}); "
                                  f"written to artifact/data/storecat__all.json (cols: month, store, category, identified, sent, listed, sold, revenue). "
                                  f"Branch: {one_line(ev_branch.get(id, ev_branch['units_sold'])).replace('{TZ}', TZ)}"})
    # KPIs: record the real SQL by running each KPI for the latest complete month through a recording proxy
    md = _kpi_md_formulas()
    con = kpi_mod.connect_harmonized()
    cols_by_table = {}
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall():
        cols_by_table[t] = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name=?", [t]).fetchall()]
    for kid, kd in kpi_mod.KPIS.items():
        rec = _Recorder(con)
        kpi_mod.compute(rec, kid, latest_complete)
        seen, stmts, used, tabs_used = {}, [], [], []
        for sql, params in rec.log:
            key = one_line(sql)
            if key in seen:
                seen[key] += 1
                continue
            seen[key] = 1
            stmts.append((key, params))
            toks = set(re.findall(r"[a-z_]+", key.lower()))
            for t, cols in cols_by_table.items():
                if t in toks:
                    if t not in tabs_used:
                        tabs_used.append(t)
                    for c in cols:
                        if c in toks and f"{t}.{c}" not in used:
                            used.append(f"{t}.{c}")
        sql_text = "\n\n".join(_inline(s, p) for s, p in stmts) if stmts else ""
        notes = [f"Code definition (goodwill_pulse/kpi.py): {kd.formula}"]
        notes.append(f"Pillar {kd.pillar}; better when {kd.better}; availability {kd.availability}; star={kd.anchor}; scorecard={kd.scorecard}.")
        notes.append(f"SQL recorded by running compute() for {latest_complete:%Y-%m} against harmonized.duckdb; "
                     f"the value for month m is computed from a {13}-month window so statements span prior months too.")
        if not stmts:
            notes.append("No SQL: no source data yet, value is always None.")
        else:
            reps = [n for n in seen.values() if n > 1]
            if reps:
                notes.append("Some statements ran several times with different parameters (for example once per category); shown once.")
        measures.append({"id": kid, "label": kd.label, "synonyms": KPI_SYN.get(kid, [kd.label.lower(), kid.replace("_", " ")]),
                         "unit": kd.unit, "kind": "kpi", "formula_text": md.get(kid) or kd.formula, "sql": sql_text,
                         "source_tables": tabs_used if stmts else [], "columns_used": used,
                         "notes": " ".join(notes), "pillar": kd.pillar, "availability": kd.availability,
                         "defined_in": "goodwill_pulse/kpi.py; docs/KPI_DEFINITIONS.md"})
    con.close()
    return {"measures": measures, "scripts": {"export_artifact_docs": str(EXPORT_SCRIPT.relative_to(ROOT.parent)),
                                              "found": bool(script)}}


# -------------------------------------------------------------------------------------------------- ledger
def native_cte(ch: str) -> tuple[str, list[str]]:
    """CTE `o(k, d, item_sales, shipping, fees, refunds)`: one row per kept order computed straight from the native
    tables with native column names (no macros, no harmonized tables). d is the America/New_York business date."""
    ny = lambda e: f"CAST(timezone('{TZ}', {e}) AS DATE)"  # noqa: E731
    if ch == "amazon":
        return (f"""WITH ev AS (SELECT * FROM amazon.financial_events QUALIFY row_number() OVER (PARTITION BY date_time, type, order_id, upper(trim(sku)), settlement_id, total, product_sales, selling_fees, other_transaction_fees ORDER BY event_id) = 1),
 fee AS (SELECT order_id, -sum(coalesce(selling_fees,0)+coalesce(fba_fees,0)+coalesce(other_transaction_fees,0)) f FROM ev WHERE type = 'Order' GROUP BY 1),
 rf AS (SELECT order_id, -sum(coalesce(product_sales,0)+coalesce(shipping_credits,0)) r FROM ev WHERE type = 'Refund' GROUP BY 1),
 ln AS (SELECT amazon_order_id, sum(item_price) s, sum(shipping_price) sh FROM (SELECT * FROM amazon.order_items QUALIFY row_number() OVER (PARTITION BY amazon_order_id, upper(trim(seller_sku)) ORDER BY order_item_id) = 1) GROUP BY 1),
 o AS (SELECT a.amazon_order_id k, {ny('CAST(a.purchase_date AS TIMESTAMPTZ)')} d, coalesce(ln.s,0) item_sales, coalesce(ln.sh,0) shipping, coalesce(fee.f,0) fees, coalesce(rf.r,0) refunds FROM amazon.orders a LEFT JOIN ln ON ln.amazon_order_id = a.amazon_order_id LEFT JOIN fee ON fee.order_id = a.amazon_order_id LEFT JOIN rf ON rf.order_id = a.amazon_order_id WHERE upper(trim(coalesce(a.buyer_email,''))) <> 'TEST' AND a.order_status <> 'Canceled')""",
                ["amazon.orders", "amazon.order_items", "amazon.financial_events"])
    if ch == "ebay":
        return (f"""WITH tx AS (SELECT * FROM ebay.transactions QUALIFY row_number() OVER (PARTITION BY orderId, transactionType, transactionDate, amount, totalFeeAmount, payoutId ORDER BY transactionId) = 1),
 sf AS (SELECT orderId, sum(coalesce(try_cast(totalFeeAmount AS DECIMAL(12,2)),0)) f FROM tx WHERE transactionType = 'SALE' GROUP BY 1),
 rf AS (SELECT orderId, sum(abs(try_cast(amount AS DECIMAL(12,2)))) r FROM tx WHERE transactionType = 'REFUND' GROUP BY 1),
 o AS (SELECT e.orderId k, {ny('CAST(e.creationDate AS TIMESTAMPTZ)')} d, coalesce(try_cast(e.pricingSummary_priceSubtotal AS DECIMAL(12,2)),0) item_sales, coalesce(try_cast(e.pricingSummary_deliveryCost AS DECIMAL(12,2)),0) shipping, coalesce(try_cast(e.totalMarketplaceFee AS DECIMAL(12,2)), sf.f, 0) fees, coalesce(rf.r,0) refunds FROM ebay.orders e LEFT JOIN sf ON sf.orderId = e.orderId LEFT JOIN rf ON rf.orderId = e.orderId WHERE upper(trim(coalesce(e.buyer_username,''))) <> 'TEST')""",
                ["ebay.orders", "ebay.transactions"])
    if ch == "shopgoodwill":
        return (f"""WITH s AS (SELECT * FROM shopgoodwill.sales QUALIFY row_number() OVER (PARTITION BY OrderID, ItemID ORDER BY PaidDate) = 1),
 fe AS (SELECT OrderID, sum(Amount) f FROM (SELECT * FROM shopgoodwill.seller_fees QUALIFY row_number() OVER (PARTITION BY OrderID, ItemID, FeeType, Amount, FeeDate ORDER BY FeeID) = 1) WHERE OrderID IS NOT NULL GROUP BY 1),
 o AS (SELECT CAST(s.OrderID AS VARCHAR) k, {ny("timezone('America/Los_Angeles', min(s.PaidDate))")} d, sum(coalesce(s.HammerPrice,0)) item_sales, sum(coalesce(s.ShippingCharged,0)) shipping, coalesce(any_value(fe.f),0) fees, sum(coalesce(s.RefundAmount,0)) refunds FROM s LEFT JOIN fe ON fe.OrderID = s.OrderID GROUP BY s.OrderID HAVING NOT bool_or(upper(trim(coalesce(s.BuyerID,''))) = 'TEST'))""",
                ["shopgoodwill.sales", "shopgoodwill.seller_fees"])
    if ch == "goodwillfinds":
        return (f"""WITH g AS (SELECT * FROM goodwillfinds.orders QUALIFY row_number() OVER (PARTITION BY coalesce(name, CAST(id AS VARCHAR)) ORDER BY id) = 1),
 rf AS (SELECT order_id, sum(abs(amount)) r FROM (SELECT * FROM goodwillfinds.refunds QUALIFY row_number() OVER (PARTITION BY order_id, created_at, amount ORDER BY id) = 1) GROUP BY 1),
 o AS (SELECT CAST(g.id AS VARCHAR) k, {ny('CAST(g.created_at AS TIMESTAMPTZ)')} d, coalesce(g.subtotal_price,0) item_sales, coalesce(g.total_shipping_price,0) shipping, coalesce(g.marketplace_fee,0) fees, coalesce(rf.r,0) refunds FROM g LEFT JOIN rf ON rf.order_id = g.id WHERE upper(trim(coalesce(g.email_hash,''))) <> 'TEST' AND upper(coalesce(g.name,'')) NOT LIKE '%TEST%')""",
                ["goodwillfinds.orders", "goodwillfinds.refunds"])
    if ch == "goodwillbooks":
        return ("""WITH o AS (SELECT s.order_no k, CAST(strptime(trim(s.order_date), '%m/%d/%Y %H:%M') AS DATE) d, s.items_cents/100.0 item_sales, s.shipping_cents/100.0 shipping, s.fee_cents/100.0 fees, abs(coalesce(s.refund_cents,0))/100.0 refunds FROM goodwillbooks.sales_orders s WHERE upper(trim(coalesce(s.customer_ref,''))) <> 'TEST')""",
                ["goodwillbooks.sales_orders"])
    raise ValueError(ch)


MEASURES = ["item_sales", "orders", "shipping", "fees", "refunds"]
DOC_COL = {"orders": "orders", "item_sales": "item_sales", "shipping": "shipping", "fees": "fees", "refunds": "refunds"}
HCOL = {"item_sales": "subtotal", "shipping": "shipping_charged", "fees": "marketplace_fees", "refunds": "refund_amount"}
TRUTH = {"item_sales": "subtotal", "shipping": "shipping_charged", "fees": "(marketplace_fee + payment_fee)"}


def pred(col: str, scope: dict) -> str:
    if "date" in scope:
        return f"{col} = DATE '{scope['date']}'"
    return f"{col} BETWEEN DATE '{scope['from']}' AND DATE '{scope['to']}'"


def run_path(con, label, sql, tables):
    sql1 = one_line(sql)
    n, v = con.execute(sql1).fetchone()
    return {"label": label, "sql": sql1, "value": num(v) if v is not None else 0.0, "rows_scanned": int(n or 0),
            "source_tables": tables}


def py_path(label, desc, value, rows, tables):
    return {"label": label, "sql": desc, "value": num(value), "rows_scanned": int(rows), "source_tables": tables}


def evaluate(paths, page_value, tol):
    ref = page_value if page_value is not None else paths[0]["value"]
    devs = {p["label"]: round(p["value"] - ref, 4) for p in paths}
    worst = max((abs(d) for d in devs.values()), default=0.0)
    ok = worst <= tol + 1e-9
    return ("match" if ok else "diff"), round(worst, 4), devs


def make_check(cid, title, scope, measure, paths, page_value, page_source, tol, hints=None):
    status, worst, devs = evaluate(paths, page_value, tol)
    c = {"id": cid, "title": title, "scope": scope, "measure": measure, "paths": paths, "page_value": num(page_value),
         "page_source": page_source, "status": status, "tolerance": tol, "diff": worst,
         "deviations": devs if status == "diff" else {}}
    if status == "diff":
        bad = [l for l, d in devs.items() if abs(d) > tol]
        c["explanation"] = (f"Paths off the page value by more than {tol}: " + "; ".join(f"{l} ({devs[l]:+.2f})" for l in bad)
                            + ". " + (hints(bad) if hints else ""))
    return c


def doc_rows(daily_doc, channel=None, scope=None):
    cols = daily_doc["cols"]
    ix = {c: i for i, c in enumerate(cols)}
    out = []
    for r in daily_doc["rows"]:
        d = r[ix["date"]]
        if channel and r[ix["channel"]] != channel:
            continue
        if "date" in scope and d != scope["date"]:
            continue
        if "from" in scope and not (scope["from"] <= d <= scope["to"]):
            continue
        out.append(r)
    return out, ix


def channel_checks(con, daily_doc, scopes) -> list[dict]:
    checks = []
    for scope in scopes:
        for ch in CHANNELS:
            cte, ntabs = native_cte(ch)
            for m in MEASURES:
                nat_col = "item_sales" if m == "item_sales" else m
                if m == "orders":
                    nat = f"{cte} SELECT count(*), count(*) FROM o WHERE {pred('d', scope)}"
                else:
                    nat = f"{cte} SELECT count(*), round(sum({nat_col}), 2) FROM o WHERE {pred('d', scope)}"
                paths = [run_path(con, f"native source DB ({ch}: {', '.join(t.split('.')[1] for t in ntabs)})", nat, ntabs)]
                if m == "orders":
                    h = f"SELECT count(*), count(*) FROM harmonized.fct_orders WHERE channel = '{ch}' AND {pred('business_date', scope)}"
                else:
                    h = (f"SELECT count(*), round(sum({HCOL[m]}), 2) FROM harmonized.fct_orders WHERE channel = '{ch}' "
                         f"AND {pred('business_date', scope)}")
                paths.append(run_path(con, "harmonized fct_orders", h, ["harmonized.fct_orders"]))
                if m == "item_sales":
                    paths.append(run_path(con, "harmonized fct_order_lines (sum of line sale_price)",
                                          f"SELECT count(*), round(sum(sale_price), 2) FROM harmonized.fct_order_lines WHERE channel = '{ch}' AND {pred('business_date', scope)}",
                                          ["harmonized.fct_order_lines"]))
                if m == "fees":
                    paths.append(run_path(con, "harmonized fct_order_lines (sum of fee_alloc)",
                                          f"SELECT count(*), round(sum(fee_alloc), 2) FROM harmonized.fct_order_lines WHERE channel = '{ch}' AND {pred('business_date', scope)}",
                                          ["harmonized.fct_order_lines"]))
                if m == "orders":
                    paths.append(run_path(con, "harmonized fct_order_lines (distinct order_key)",
                                          f"SELECT count(*), count(DISTINCT order_key) FROM harmonized.fct_order_lines WHERE channel = '{ch}' AND {pred('business_date', scope)}",
                                          ["harmonized.fct_order_lines"]))
                if m == "refunds":
                    paths.append(run_path(con, "harmonized fct_refunds joined to the order's business date",
                                          f"SELECT count(*), round(sum(r.amount), 2) FROM harmonized.fct_refunds r JOIN harmonized.fct_orders o USING (order_key) WHERE o.channel = '{ch}' AND {pred('o.business_date', scope)}",
                                          ["harmonized.fct_refunds", "harmonized.fct_orders"]))
                # truth world: the generator's clean ledger, derived independently of the dirty source DBs
                tp = f"CAST(timezone('{TZ}', t.paid_at) AS DATE)"
                if m == "orders":
                    ts = f"SELECT count(*), count(*) FROM truth.orders t WHERE t.channel = '{ch}' AND {pred(tp, scope)}"
                elif m == "refunds":
                    ts = (f"SELECT count(*), round(sum(r.amount), 2) FROM truth.refunds r JOIN truth.orders t USING (order_id) "
                          f"WHERE t.channel = '{ch}' AND {pred(tp, scope)}")
                else:
                    ts = f"SELECT count(*), round(sum({TRUTH[m]}), 2) FROM truth.orders t WHERE t.channel = '{ch}' AND {pred(tp, scope)}"
                paths.append(run_path(con, "truth world (_truth.duckdb, generator ledger)", ts,
                                      ["truth.orders"] + (["truth.refunds"] if m == "refunds" else [])))
                rows, ix = doc_rows(daily_doc, ch, scope)
                pv = sum(r[ix[DOC_COL[m]]] for r in rows)
                sc = scope.get("month") or scope.get("date")
                src = (f"artifact/data/daily__all.json :: rows[date={'%s' % scope['date']}, channel={ch}].{DOC_COL[m]}" if "date" in scope
                       else f"artifact/data/daily__all.json :: sum(rows[date in {scope['month']}, channel={ch}].{DOC_COL[m]}) over {len(rows)} rows")
                handling = None
                if m == "shipping":
                    handling = num(con.execute(f"SELECT coalesce(sum(handling), 0) FROM truth.orders t WHERE t.channel = '{ch}' AND {pred(tp, scope)}").fetchone()[0])

                def hints(bad, ch=ch, m=m, handling=handling, paths=paths, pv=pv):
                    h = []
                    if m == "shipping" and handling:
                        tv = next((p["value"] for p in paths if p["label"].startswith("truth")), None)
                        if tv is not None and abs((pv - tv) - handling) <= MONEY_TOL:
                            return (f"Cause found: the truth world keeps shipping ({tv}) and handling ({handling}) apart, but the {ch} "
                                    f"export has one delivery field that already includes handling, so the harmonized shipping_charged "
                                    f"({pv}) equals shipping + handling. The two sources agree after adding handling; the definitions "
                                    f"differ by channel (ShopGoodwill keeps Handling in its own column, which fct_orders.handling holds "
                                    f"and the page's shipping excludes).")
                    if any("truth" in b for b in bad):
                        h.append("The truth world is the generator's ledger before native formatting and planted dirty rows; "
                                 "a difference there means the source DB export differs from truth (for example fees or shipping "
                                 "not carried into the native shape), not that the page is wrong.")
                    if any("native" in b for b in bad):
                        h.append("The native recomputation is an independent re-implementation from raw columns; a gap points at "
                                 "dedup, time zone or money parsing handled differently from the harmonize SQL.")
                    return " ".join(h)
                checks.append(make_check(
                    f"{ch}.{m}." + (scope.get("month") or scope.get("date")),
                    f"{ch} {m.replace('_', ' ')} for {sc}", dict(scope, channel=ch), m, paths, pv, src,
                    0 if m == "orders" else MONEY_TOL, hints))
                if handling is not None:
                    checks[-1]["components"] = {"truth_handling": handling}
    return checks


def build_ledger(con, latest_complete: date, as_of: date) -> dict:
    daily_doc = read_doc("daily__all.json")
    month = f"{latest_complete:%Y-%m}"
    last = (latest_complete.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    mscope = {"month": month, "from": latest_complete.isoformat(), "to": last.isoformat()}
    dscope = {"date": as_of.isoformat()}
    checks = channel_checks(con, daily_doc, [mscope, dscope])
    month_doc = read_doc(f"month__{month}.json")
    card = month_doc["cards"]["total_revenue"]["value"]

    # (b) KPI total_revenue vs sum of daily
    rec = _Recorder(con)
    # kpi.compute needs a connection-like object with .execute; the lab connection has harmonized attached as a catalog,
    # so use a separate read-only handle for the KPI path.
    kcon = kpi_mod.connect_harmonized()
    krec = _Recorder(kcon)
    kval = kpi_mod.compute(krec, "total_revenue", latest_complete)
    ksql = next((_inline(s, p) for s, p in krec.log if "SUM(subtotal)" in s and "fct_orders" in s), "")
    kcon.close()
    rows, ix = doc_rows(daily_doc, None, mscope)
    daily_sum = sum(r[ix["item_sales"]] for r in rows)
    native_sum = round(sum(next(p["value"] for p in c["paths"] if p["label"].startswith("native")) for c in checks
                           if c["measure"] == "item_sales" and c["scope"].get("month") == month), 2)
    pb = [py_path("KPI total_revenue (goodwill_pulse.kpi.compute)", ksql, kval, 1, ["harmonized.fct_orders"]),
          run_path(con, "harmonized fct_orders sum(subtotal)", f"SELECT count(*), round(sum(subtotal), 2) FROM harmonized.fct_orders WHERE {pred('business_date', mscope)}", ["harmonized.fct_orders"]),
          run_path(con, "harmonized fct_order_lines sum(sale_price)", f"SELECT count(*), round(sum(sale_price), 2) FROM harmonized.fct_order_lines WHERE {pred('business_date', mscope)}", ["harmonized.fct_order_lines"]),
          py_path("sum of daily/all item_sales (the page's own daily doc)", f"python: sum(item_sales) over {len(rows)} rows of daily__all.json with date in {month}", daily_sum, len(rows), ["artifact/data/daily__all.json"]),
          py_path("sum of the five native-DB recomputations (check (a), item_sales)", "python: sum of the native path values of the five item_sales month checks above", native_sum, 5, MARKETPLACE_DBS)]
    checks.append(make_check(f"kpi.total_revenue.{month}", f"KPI total_revenue for {month} vs sum of daily", {"month": month}, "total_revenue", pb, card,
                             f"artifact/data/month__{month}.json :: cards.total_revenue.value", MONEY_TOL))

    # (c) store revenue sums to total revenue
    stores_sum = sum(s["revenue"] for s in month_doc["stores"]["stores"])
    sc_doc = read_doc("storecat__all.json")
    sc_rows = [r for r in sc_doc["rows"] if r[0] == month]
    sc_ix = {c: i for i, c in enumerate(sc_doc["cols"])}
    pc = [py_path("sum of stores[].revenue in the month doc (what the Stores view shows)", f"python: sum(stores[].revenue) over {len(month_doc['stores']['stores'])} stores in month__{month}.json", stores_sum, len(month_doc["stores"]["stores"]), ["artifact/data/month__%s.json" % month]),
          py_path("sum of storecat/all revenue for the month", f"python: sum(revenue) over {len(sc_rows)} month x store x category rows of storecat__all.json", sum(r[sc_ix["revenue"]] for r in sc_rows), len(sc_rows), ["artifact/data/storecat__all.json"]),
          run_path(con, "fct_order_lines revenue credited to a store (store_id not null)", f"SELECT count(*), round(sum(sale_price), 2) FROM harmonized.fct_order_lines WHERE store_id IS NOT NULL AND {pred('business_date', mscope)}", ["harmonized.fct_order_lines"]),
          run_path(con, "fct_order_lines revenue with NO store (store_id null)", f"SELECT count(*), round(coalesce(sum(sale_price), 0), 2) FROM harmonized.fct_order_lines WHERE store_id IS NULL AND {pred('business_date', mscope)}", ["harmonized.fct_order_lines"]),
          run_path(con, "fct_order_lines revenue, all lines", f"SELECT count(*), round(sum(sale_price), 2) FROM harmonized.fct_order_lines WHERE {pred('business_date', mscope)}", ["harmonized.fct_order_lines"])]
    # the store-less path is a component, not a total; the identity under test is stores + unassigned = total
    unassigned = pc[3]["value"]
    pc[3]["label"] += " (component: stores + unassigned = total)"
    c = make_check(f"store_revenue.sum.{month}", f"Store revenue sums to total revenue for {month}", {"month": month}, "store_revenue",
                   [p for i, p in enumerate(pc) if i != 3], card, f"artifact/data/month__{month}.json :: cards.total_revenue.value", MONEY_TOL,
                   lambda bad: "Revenue on lines with no resolvable store is not credited to any store, so the Stores view can sum to less "
                               "than the headline by exactly that amount; see the unassigned path.")
    c["components"] = {"unassigned_store_revenue": unassigned}
    c["paths"].append(pc[3])
    checks.append(c)

    # (d) payouts net = gross - fees - refunds, per channel for the month, vs the close doc
    close = read_doc(f"close__{month}.json")
    cb = {b["channel"]: b for b in close["customer_balances"]}
    nat_pay = {
        "amazon": ("amazon.settlements", f"SELECT count(*), round(sum(total_amount), 2) FROM amazon.settlements WHERE deposit_date BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "ebay": ("ebay.payouts", f"SELECT count(*), round(sum(try_cast(amount AS DECIMAL(12,2))), 2) FROM ebay.payouts WHERE CAST(timezone('{TZ}', CAST(payoutDate AS TIMESTAMPTZ)) AS DATE) BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "shopgoodwill": ("shopgoodwill.periodic_statements", f"SELECT count(*), round(sum(NetRemit), 2) FROM shopgoodwill.periodic_statements WHERE RemitDate BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "goodwillfinds": ("goodwillfinds.payouts", f"SELECT count(*), round(sum(amount), 2) FROM goodwillfinds.payouts WHERE date BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "goodwillbooks": ("goodwillbooks.monthly_statements", f"SELECT count(*), round(sum(net_cents) / 100.0, 2) FROM goodwillbooks.monthly_statements WHERE paid_on BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
    }
    nat_calc = {
        "amazon": ("amazon.settlements", f"SELECT count(*), round(sum(total_amount), 2) FROM amazon.settlements WHERE deposit_date BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "ebay": None,
        "shopgoodwill": ("shopgoodwill.periodic_statements", f"SELECT count(*), round(sum(GrossSales - Commission - PaymentFees - Refunds), 2) FROM shopgoodwill.periodic_statements WHERE RemitDate BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "goodwillfinds": ("goodwillfinds.payouts", f"SELECT count(*), round(sum(charges_gross - fees - refunds_gross), 2) FROM goodwillfinds.payouts WHERE date BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
        "goodwillbooks": ("goodwillbooks.monthly_statements", f"SELECT count(*), round(sum(gross_cents - fees_cents - refunds_cents) / 100.0, 2) FROM goodwillbooks.monthly_statements WHERE paid_on BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"),
    }
    for ch in CHANNELS:
        pw = f"channel = '{ch}' AND paid_on BETWEEN DATE '{mscope['from']}' AND DATE '{mscope['to']}'"
        paths = [run_path(con, "harmonized fct_payouts sum(net)", f"SELECT count(*), round(sum(net), 2) FROM harmonized.fct_payouts WHERE {pw}", ["harmonized.fct_payouts"]),
                 run_path(con, "harmonized fct_payouts sum(gross - fees - refunds)", f"SELECT count(*), round(sum(gross - fees - refunds), 2) FROM harmonized.fct_payouts WHERE {pw}", ["harmonized.fct_payouts"]),
                 run_path(con, f"native payout amount ({nat_pay[ch][0]})", nat_pay[ch][1], [nat_pay[ch][0]])]
        if nat_calc[ch]:
            paths.append(run_path(con, f"native gross - fees - refunds ({nat_calc[ch][0]})", nat_calc[ch][1], [nat_calc[ch][0]]))
        checks.append(make_check(f"payouts.net.{ch}.{month}", f"{ch} payouts: net = gross - fees - refunds ({month}, by pay date)",
                                 {"month": month, "channel": ch}, "payout_net", paths, cb[ch]["payouts"],
                                 f"artifact/data/close__{month}.json :: customer_balances[channel={ch}].payouts", MONEY_TOL,
                                 lambda bad: "Payouts are dated by pay date; a gap against the native statement means the statement rows and the harmonized rows disagree on a component."))

    # (e) pulse (report files) vs harmonized, latest 7 days
    pulse = read_doc("pulse__all.json")
    days = sorted(pulse, reverse=True)[:7]
    for d in sorted(days):
        sc = {"date": d}
        tot = pulse[d]["total"]["revenue"]
        paths = []
        if "warehouse" in {r[0] for r in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}:
            paths.append(run_path(con, "Daily Pulse warehouse (report files: warehouse.orders)",
                                  f"SELECT count(*), round(sum(subtotal), 2) FROM warehouse.orders WHERE business_date = DATE '{d}'", ["warehouse.orders"]))
        paths.append(run_path(con, "harmonized fct_orders", f"SELECT count(*), round(sum(subtotal), 2) FROM harmonized.fct_orders WHERE business_date = DATE '{d}'", ["harmonized.fct_orders"]))
        nat = []
        for ch in CHANNELS:
            cte, tabs = native_cte(ch)
            nat.append(f"SELECT d, item_sales FROM ({cte} SELECT d, item_sales FROM o)")
        paths.append(run_path(con, "native source DBs, all five channels",
                              "SELECT count(*), round(sum(item_sales), 2) FROM (" + " UNION ALL ".join(nat) + f") WHERE d = DATE '{d}'", MARKETPLACE_DBS))
        paths.append(py_path("daily/all doc, all channels", f"python: sum(item_sales) of daily__all.json rows with date = {d}",
                             sum(r[3] for r in daily_doc["rows"] if r[0] == d), sum(1 for r in daily_doc["rows"] if r[0] == d), ["artifact/data/daily__all.json"]))
        def phint(bad, d=d):
            h = ["The Daily Pulse is built from the Upright and Cash Monkey report files, a separate ingest from the marketplace APIs."]
            if d == as_of.isoformat():
                h.append("The latest day is still open (the report covers up to 22:00 ET), so late orders may differ.")
            return " ".join(h)
        checks.append(make_check(f"pulse.item_sales.{d}", f"Daily Pulse revenue vs harmonized for {d}", sc, "item_sales", paths, tot,
                                 f"artifact/data/pulse__all.json :: {d}.total.revenue", MONEY_TOL, phint))
    return {"as_of": as_of.isoformat(), "latest_complete_month": month, "tolerance_note": f"Money within {MONEY_TOL} (half a cent), counts exact.",
            "summary": {"checks": len(checks), "match": sum(c["status"] == "match" for c in checks),
                        "diff": sum(c["status"] == "diff" for c in checks)},
            "checks": checks}


# ---------------------------------------------------------------------------------------------------- drill
def build_drill(con, as_of: date) -> dict:
    days = [as_of - timedelta(days=i) for i in range(DRILL_DAYS - 1, -1, -1)]
    out = {}
    for d in days:
        for ch in CHANNELS:
            tot = con.execute("""SELECT count(*), round(coalesce(sum(subtotal),0),2), round(coalesce(sum(shipping_charged),0),2),
                                        round(coalesce(sum(marketplace_fees),0),2), round(coalesce(sum(refund_amount),0),2)
                                 FROM harmonized.fct_orders WHERE business_date = ? AND channel = ?""", [d, ch]).fetchone()
            rows = con.execute(f"""SELECT order_key, strftime(timezone('{TZ}', paid_at_utc), '%Y-%m-%d %H:%M:%S'),
                                          subtotal, shipping_charged, marketplace_fees, refund_amount, source_db, marketplace_order_id
                                   FROM harmonized.fct_orders WHERE business_date = ? AND channel = ?
                                   ORDER BY subtotal DESC, order_key LIMIT {DRILL_CAP}""", [d, ch]).fetchall()
            out[f"{d.isoformat()}|{ch}"] = {
                "total_rows": tot[0], "sum_item_sales": float(tot[1]), "sum_shipping": float(tot[2]), "sum_fees": float(tot[3]),
                "sum_refunds": float(tot[4]), "shown_rows": len(rows), "truncated": tot[0] > len(rows),
                "shown_item_sales": round(sum(float(r[2]) for r in rows), 2),
                "rows": [{"order_key": r[0], "paid_at_et": r[1], "item_sales": float(r[2]), "shipping": float(r[3]),
                          "fees": float(r[4]), "refund": float(r[5]), "source_db": r[6], "native_key": r[7]} for r in rows]}
    return out


# ----------------------------------------------------------------------------------------------------- main
def main(out: Path | None = None) -> dict:
    out = Path(out) if out else ART_DATA
    out.mkdir(parents=True, exist_ok=True)
    con = lab_connection()
    as_of = con.execute("SELECT max(business_date) FROM harmonized.fct_orders").fetchone()[0]
    month_end = lambda m: (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)  # noqa: E731
    cur = as_of.replace(day=1)
    latest_complete = cur if month_end(cur) <= as_of else (cur - timedelta(days=1)).replace(day=1)
    sizes = {}
    sizes["lineage__drill.json"] = put(out, "drill", build_drill(con, as_of))
    sizes["lineage__metrics.json"] = put(out, "metrics", build_metrics(latest_complete))
    sizes["lineage__ledger.json"] = put(out, "ledger", build_ledger(con, latest_complete, as_of))
    con.close()
    sizes["lineage__overview.json"] = 0
    for _ in range(3):  # overview lists its own size: iterate until the digits settle
        ov = build_overview(sizes)
        s = len(dumps(ov).encode())
        if s == sizes["lineage__overview.json"]:
            break
        sizes["lineage__overview.json"] = s
    sizes["lineage__overview.json"] = put(out, "overview", ov)
    return sizes


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
