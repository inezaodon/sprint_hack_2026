# Data quality and reconciliation

Owner: Engineer 5. Code: `sql/checks/*.sql`, `goodwill_pulse/quality.py`, `tests/test_quality.py`.

```
.venv/bin/python -m goodwill_pulse.quality                       # run all checks, write dq_results, exit 1 on any error-severity failure
.venv/bin/python -m goodwill_pulse.quality --details             # also print example failing rows
.venv/bin/python -m goodwill_pulse.quality --reconcile-reports   # also compare the Daily Pulse (report files) with harmonized
.venv/bin/python -m pytest -q tests/test_quality.py
```

Each check is one file, `sql/checks/<check_id>.sql`, with a `-- severity:` and a `-- description:` header and a
single SELECT that returns the **failing rows** (zero rows = pass). Files starting with `_` are helpers, not checks:
`_prelude.sql` builds TEMP views used by several checks (`dq_native_orders`, `dq_native_lines`, `dq_dirty`).
Checks run against `harmonized.duckdb` (attached read-only) with the source DBs attached read-only as `amazon, ebay,
shopgoodwill, goodwillfinds, goodwillbooks, ops`. Results replace the `dq_results` table: one row per check with
status, failing row count and up to 20 example rows. The table is written to a copy that is then `os.replace`d onto
the file, so readers never see a half-written DB and the live file is never held open read-write.
If a check's SQL errors, it is recorded as `fail` with `failing_rows = NULL` and the error text in `detail`. It is
never reported as a pass.

**Severity:** `error` = the numbers can't be trusted until this is fixed; the build exits 1. `warning` = an
exception that finance should look at (the build continues). `info` = for information only.

| check_id | severity | what it checks | why it matters to Goodwill's finance team |
|---|---|---|---|
| `orders_duplicate_key` | error | No duplicate `order_key`, and no marketplace order id loaded under two keys | A duplicated order inflates revenue and customer counts on the Pulse, dashboard and journal |
| `order_lines_orphan` | error | Every line belongs to a loaded order, and every order has lines | Store and category revenue is built from lines and channel revenue from orders, so the two must reconcile |
| `order_subtotal_vs_lines` | error | Order subtotal = Σ line `sale_price`; `item_count` = Σ quantity | Store and category splits must add back up to the channel total that finance reports |
| `fee_alloc_vs_order_fees` | error | Σ line `fee_alloc` = order `marketplace_fees` (to the cent) | Store-level net margin and fee allocations in the close journal must equal the fees actually charged |
| `order_total_formula` | error | `total` = subtotal + shipping + handling + tax | Gross receipts and the AR invoice use `total`. A wrong total breaks the bank deposit match |
| `store_id_missing` | warning | Lines, listings and items credited to a known store (`store_id` not NULL / in `dim_store`) | Each store gets credit for what it donated. A NULL store is an open exception to fix, not a silent drop |
| `unknown_category_channel` | error | Channel is one of the 5 canonical channels (and in `dim_channel`); category is one of the 10 canonical categories | A typo like "jewelry " or "Ebay" creates a phantom row on the dashboard and misroutes close rules |
| `business_date_et` | error | `business_date` = America/New_York date of the UTC timestamp (orders, refunds); lines carry their order's date | A late-evening Pacific sale belongs to the Eastern business day the store report uses, and month-end cut-off depends on it |
| `refunds_vs_orders` | error | Refunds point at a loaded order, never exceed the order total, and equal `fct_orders.refund_amount`; no negative refunds | Unmatched or oversized refunds misstate net revenue and break the refunds line of each payout |
| `payouts_vs_orders` | warning | Payout net = gross − fees − refunds; gross matches the channel's orders, fees match Σ order `marketplace_fees` or Σ `fct_fees` (all types, incl. eBay shipping labels), and refunds match `fct_refunds` for the payout period (gross basis: subtotal, subtotal+shipping+handling, or total; tolerance max($1, 1%)) | Deposits into 1st Source account 0101 must tie back to sales. A gap means missing orders, missed fees or a period cut-off error |
| `listing_order_link` | error | Every sold listing has an order line (same channel + SKU), and every order line has a sold listing on that channel | Sell-through and days-to-sell KPIs and the item lifecycle depend on the listing ↔ sale link |
| `item_sold_twice` | error | A SKU is on at most one order line and has at most one sold listing | A donated item is unique. Selling it twice is double counting or a cross-channel oversell |
| `source_reconciliation` | error | Per source DB: native order count, line count and item subtotal = harmonized, with `_dirty_data` orders excluded on both sides and Amazon `Canceled` orders excluded natively | Proves that nothing was lost or invented between the marketplace export and the numbers finance sees |
| `dirty_data_handled` | error | Every `_dirty_data` row is excluded, flagged (NULL store) or fixed. A test order or test listing that was loaded, a duplicate that was double counted, a non-canonical SKU/store/category that was loaded with a store, or an untraceable `native_key` fails | Shows that the harmonizer catches known dirt (test orders, duplicate API rows, SKU and vendor typos) instead of loading it silently |
| `ebay_double_counting` | error | Each eBay order is counted once, its `line_of_business` matches its SKUs (UP- = Upright general merch, GWM- = Cash Monkey books), and Upright/Cash Monkey line totals match native eBay | Both tools sell through one eBay account, so the same sale must not show up in both tools' revenue |
| `month_completeness` | warning | Every business day from the first to the last order has ≥ 1 order on every channel | A zero day almost always means a missing feed, and a month with gaps should not be closed |
| `budget_coverage` | warning | Every month with orders has a positive revenue budget for every channel | Without a budget, budget-vs-actual is blank instead of showing a variance |

## Report reconciliation (Daily Pulse vs harmonized)

`reconcile_reports(harmonized_path, warehouse_path)` compares the Daily Pulse warehouse (built from the Upright and
Cash Monkey report files) with harmonized marketplace data. It works per business_date × Pulse row
(`config/channels.yaml`) on the revenue basis (`subtotal`), over the dates the warehouse has data for.
Each cell's status is `match`, `mismatch`, `missing_report` or `missing_harmonized`. `double_counted_orders`
counts marketplace orders that appear in **both** the Upright and the Cash Monkey report, which is the eBay shared-account risk.
`warehouse.duckdb` is opened read-only. If the live server's lock blocks that, a temporary copy next to the file is
read and then deleted.

## Conventions the checks rely on

- `_dirty_data.native_key` is the native primary key as text. For composite keys the parts are joined with `/` or `:`,
  order id first (`GWB123456/2`, `9004/5010`, `113-…/I1`). Line, transaction, fee and refund ids are resolved to
  their order through the native tables. Listing tables (`auctions`, `listings`, `products`, `inventory`) map to
  `fct_listings.listing_key = '<source>:<native_key>'`.
- `kind` containing `test` must be excluded, and `kind` containing `dup` must not be double counted. For any other kind
  the row is fine if it was excluded, flagged with a NULL store, or fixed to canonical values.
- A missing source DB is replaced by an empty stand-in built from its DDL, so the checks still run. The gap then
  shows up as a reconciliation failure, and the CLI prints a warning.
