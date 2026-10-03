# Lineage export ("how did we get these numbers")

`artifact/export_lineage.py` writes four JSON docs that back the audit view. It is deterministic code over read-only
DuckDB files. No model is involved and no number is typed in by hand.

    .venv/bin/python artifact/export_lineage.py            # writes artifact/data/lineage__*.json
    .venv/bin/python artifact/export_lineage.py <out_dir>  # write somewhere else
    .venv/bin/python -m pytest -q tests/test_lineage_export.py

Run it after `tools/export_artifact_docs.py`, because the ledger compares against the docs in `artifact/data`
(daily, month, storecat, pulse, close). Each doc must stay under 250 KB; the exporter stops if one does not.
If `data/warehouse.duckdb` is locked by the API server, the exporter reads a snapshot copy from `data/_tmp/lineage/`.

## Docs (collection `lineage`)

- `overview`: pipeline steps (truth world, 7 source DBs, staging, canonical tables, quality, aggregates, Daily Pulse, docs,
  page) with the tables each touches and what was excluded on purpose; every table in `data/sources/*.duckdb` and
  `harmonized.duckdb` with row count, columns and types, grain and a one-line description; every file in `artifact/data` with
  byte size and contents; the latest harmonize run and quality run; `exclusions` (test orders, test SKUs, canceled orders)
  from `harmonize_runs.source_row_counts`.
- `metrics`: the concept map. One entry per measure with synonyms, formula in words, exact SQL, source tables and columns.
  Daily measures (item_sales, orders, shipping, fees, refunds, avg_order) use the SQL read from `tools/export_artifact_docs.py`.
  Pipeline measures (store_revenue, units_sold, items_identified, items_sent, items_listed) use its storecat SQL.
  KPIs come from `goodwill_pulse.kpi.KPIS`: the SQL is recorded by running each KPI through a recording connection for the
  latest complete month (so it is the SQL that actually ran), and formulas come from `docs/KPI_DEFINITIONS.md`.
- `ledger`: independent recomputation. Each check has two or more `paths` (label, SQL, value, rows scanned, tables), the
  value stored in the artifact docs (`page_value`, `page_source`), a `status` of `match` or `diff`, the `tolerance`
  (half a cent for money, exact for counts), the largest `diff`, and for diffs an `explanation`.
  - Per marketplace (5) x measure (item_sales, orders, shipping, fees, refunds) for the latest complete month and the latest
    day: native source DBs (raw column names, ET business date) vs `fct_orders` (and lines or refunds where applicable) vs the
    generator's truth world vs `daily__all.json`.
  - KPI `total_revenue` for the month vs sum of daily, line sums and the native totals.
  - Store revenue (month doc stores, storecat, order lines) vs total revenue. Lines with no store are listed as a component.
  - Payouts per channel for the month: `fct_payouts` net, gross - fees - refunds, and the native payout tables, vs the close doc.
  - Daily Pulse (report files, `warehouse.orders`) vs harmonized and native, for the latest 7 days.
  A check marked `component: true` on a path is informational and is not compared against the page value.
  If an independent path disagrees it is reported as `diff`; the code is never tuned to force a match.
- `drill`: keys `YYYY-MM-DD|channel` for the last 7 days x 5 channels: `total_rows`, sums, and up to 60 order rows (largest item
  sales first) with `source_db` and `native_key`. `shown_rows` and `truncated` say when a cell was cut at 60 rows;
  `sum_*` fields always cover all orders, not just the shown ones.

## Known definitions the ledger surfaces

- Shipping: ShopGoodwill keeps Handling in its own column (not in the page's shipping), while eBay and GoodwillFinds send one
  delivery field that already includes handling. The truth-world path therefore differs from the page for those two channels
  by exactly the handling amount, and the ledger says so.
- Refunds on the page (daily doc) sit on the order's date. The month-end close and `fct_refunds` date a refund by when the
  money went back.
- The latest day is open: the Pulse report covers up to 22:00 ET.
