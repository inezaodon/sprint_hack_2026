# Trust engine (Aubrey)

Fixed formulas for every metric, plus automatic checks on every result. The AI never does math: the intelligence
engine picks a metric id, filters, breakdown and period from the catalog, and this code computes the number.

Code: `goodwill_pulse/trust/` · Tests: `tests/test_trust.py`

## Run
```bash
.venv/bin/python -m goodwill_pulse.trust                       # pass/fail table on the built-in fixture
.venv/bin/python -m goodwill_pulse.trust --break total_sales   # sabotage a formula: the table goes red, exit 1
.venv/bin/python -m goodwill_pulse.trust --odin                # on Odin's data/harmonized.duckdb (read-only)
.venv/bin/python -m goodwill_pulse.trust --catalog             # metric catalog JSON (for Stardess / Victoria)
# LATER (needs Odin's sales_line + books tables and Stardess's answers file; replace YOUR_FILE):
# .venv/bin/python -m goodwill_pulse.trust --db data/YOUR_FILE.duckdb --answers answers.json
.venv/bin/python -m pytest -q tests/test_trust.py
```

## API
```python
from goodwill_pulse.trust import compute, catalog_json, dimension_values, TrustError
compute(con, "items_sold", filters={"channel": "ShopGoodwill", "department": "Jewelry"},
        breakdown=None, period="yesterday")
```
Returns `value`, `rows` (one `{key, value}` per breakdown group), `formula`, `math` (e.g. `$260.00 ÷ 5 orders = $52.00`),
`row_count`, `rows_excluded`, `source_files`, `checks` (`{id, label, ok, detail}`) and `verified`.
Raises `TrustError` with a user-safe message: unknown metric → "We don't track that yet."; labor or pieces per hour
→ "not connected yet"; a breakdown or filter the metric doesn't allow → explains why.

Periods: `today`, `yesterday`, `this_week`, `last_week`, `this_month`, `last_month`, `last_7_days`, `last_N_weeks`,
`all`, or `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`. "Today" defaults to the day after the latest data day.
Weeks start on Monday.

## Metrics (catalog v1)
total_sales (refunds subtracted) · gross_sales (before refunds, the basis of Goodwill's report and Odin's revenue) ·
refunds · orders · average_sale · shipping_charged · items_sold · customers · books_sold · books_revenue ·
books_scanned. "Sales by platform" is `total_sales` broken down by `channel`. "Items by department" is `items_sold`
broken down by `department`.

## Checks on every result
| check | what it proves |
|---|---|
| recount | an independent SQL query gets the same value for every group and for the total |
| parts_equal_whole | breakdown values add up to the total (additive metrics) |
| unpaid_excluded | unpaid rows change nothing ("if it's not paid for, it doesn't count") |
| refunds_netted | total = paid − refunds, and refund amounts are negative |
| average_identity | average sale × orders = total sales |
| departments_platforms_file | Σ by department = Σ by platform = file total |
| data checks | no duplicate lines, no missing department/platform, refunds negative |

## Running on Odin's data
`goodwill_pulse/trust/odin.py` opens `data/harmonized.duckdb` read-only and adds temporary views
(`sql/trust/views.sql`) that present his `fct_*` tables as `sales_line` and `books`. His pipeline is untouched.
`--odin` then checks three things:
1. **Planted answers from Odin's own answer key**: the Friday Eastern-time Upright export
   (`demo_data/02_friday_2026-10-02/expected.json`), which uses the same day boundary as his database.
2. **The engine's checks** on every one of those answers.
3. **Cross-check against his `kpi.py`**: monthly item sales, buyers and average selling price, total and per
   platform, must match. They match for all 14 months on every platform.

Mapping: lines come from `fct_order_lines` and `fct_orders`. Shipping is per order, so it goes on the first line.
Refunds (`fct_refunds`) become negative rows on the refund date, split across the order's lines with the rounding
remainder on the first line (they add back up exactly). Books scanned = book items in `fct_items` by
`identified_at` week. Departments are mapped from his 10 categories with a **placeholder** map in `odin.py`:
Jewelry + Watches → Jewelry, Clothing + Shoes → Textiles, everything else except Books → Wares/Hard Goods.

**Findings on Odin's data (current run: 66/69 pass):**
- Shipping isn't defined the same way across platforms. eBay and GoodwillFinds shipping includes Upright's $3/item
  handling fee (their native data folds it in, per the comments in `sql/harmonize/21_stg_ebay.sql`). ShopGoodwill's
  doesn't. On 10/02 that's +$33 on eBay and +$15 on GoodwillFinds compared with the Upright report. This affects
  demo question 3 (shipping by platform).
- One eBay line (`UP--004437`, 2026-06-06) has no store or category, so it has no department. His
  `store_id_missing` warning flags it too.
- His data has no stores scanning zero books, so "Why are book sales down?" has nothing to find yet. The plan has
  Odin plant 3 zero-scan stores.

## Need from Odin (data contract)
1. `status` values: the engine counts `paid` and `refund` and excludes everything else. Are those the right strings?
2. Refunds: a separate row with the same `order_id`, NEGATIVE `subtotal`/`shipping_charged`/`items`, dated on the
   refund date?
3. Please add a `source_file` column to `sales_line` and `books`. It feeds "source file" in show-the-math.
4. `books.week` = the Monday date of the week?
5. Which DuckDB file holds the two tables?

## Need from Stardess (planted answers)
A JSON list in this shape (see `fixture.ANSWERS`). `expect` is a number, or `{key: value}` for a breakdown:
```json
[{"question": "How many jewelry pieces sold on ShopGoodwill yesterday?", "metric": "items_sold",
  "filters": {"channel": "ShopGoodwill", "department": "Jewelry"}, "period": "yesterday", "expect": 3}]
```

## Known limits
See `KNOWN_LIMITS` in `trust/catalog.py`. Customers can't be matched across platforms. Books are weekly, so there's
no daily book breakdown. Refunds count on the day they're issued. Labor, payroll and pieces per hour aren't connected.
