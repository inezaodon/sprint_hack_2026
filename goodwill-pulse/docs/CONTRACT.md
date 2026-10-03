# Team contract (read this first)

Written by the PM (orchestrator) Sat Oct 3 2026. 10 engineers build in parallel against these interfaces.
**If an interface here is wrong or missing something you need, do not silently change another engineer's file:
add what you need to your own files, and say so clearly in your final report** (the PM merges contract changes).

Background: `../PLAN.md` (whole project) and `docs/PLAN_dashboard_and_close.md` (Features 2 and 3).
Feature 1 (Daily Pulse from Upright/Cash Monkey report files → `data/warehouse.duckdb`) already works; don't break it.

## Data flow

```
gen/truth.py ──► data/sources/_truth.duckdb   (one consistent synthetic world, 2025-09-01 .. 2026-10-03)
                    │
   sources/*.py ────┼──► data/sources/amazon.duckdb        (sql/sources/amazon.sql)
   (native shapes)  ├──► data/sources/ebay.duckdb          (sql/sources/ebay.sql)
                    ├──► data/sources/shopgoodwill.duckdb  (sql/sources/shopgoodwill.sql)
                    ├──► data/sources/goodwillfinds.duckdb (sql/sources/goodwillfinds.sql)
                    ├──► data/sources/goodwillbooks.duckdb (sql/sources/goodwillbooks.sql)
                    ├──► data/sources/ops.duckdb           (sql/sources/ops.sql)
                    └──► data/sources/finance.duckdb       (bank 0101, FedEx, jewelry, GWB statement; close engineer defines)
                                │
   sql/harmonize/*.sql ─────────┴──► data/harmonized.duckdb (sql/harmonized/schema.sql)  ◄── sql/checks/*.sql → dq_results
                                          │
                     kpi.py ──► routes/dashboard.py + web/dashboard.html     (Feature 2)
                     close/* ─► routes/close.py + web/close.html              (Feature 3)
                     ai/* ────► routes/ai.py (narrative, ask-the-data, mapper)
```

Single build command (PM owns): `.venv/bin/python -m goodwill_pulse.build` runs truth → sources → harmonize → checks.
Each stage must also run alone: `python -m goodwill_pulse.gen.truth`, `python -m goodwill_pulse.sources.<name>`,
`python -m goodwill_pulse.harmonize`, `python -m goodwill_pulse.quality`.

## Rules of engagement
- Work only inside `/Users/odon/Desktop/Hackathon/goodwill-pulse`. Never write or delete outside it.
- Python: `.venv/bin/python` (3.12). Installed: pandas, numpy, duckdb, fastapi, httpx, openpyxl, pyyaml, anthropic, pytest.
  **Do not pip install.** Need a package? Say so in your report and work around it.
- Only edit files you own (table below). Shared files (`api.py`, `web/index.html`, `db.py`, `config.py`, this doc,
  `sql/sources/*.sql`, `sql/harmonized/schema.sql`) are PM-owned.
- A uvicorn server with `--reload` is running on :8000 and holds a lock on `data/warehouse.duckdb`. Don't open that file
  for writing, don't bind port 8000; test APIs with `fastapi.testclient.TestClient`.
- DuckDB writers: build into `<file>.tmp` then `os.replace` onto the target, so readers never see half a DB.
  API code opens `harmonized.duckdb` **read_only=True** per request.
- Tests: your own `tests/test_<area>.py`, small hand-built fixtures (build tiny DBs in `tmp_path` from the DDL files),
  fast (< 10 s). Run `.venv/bin/python -m pytest -q tests/test_<area>.py`; run the full suite at the end and report
  failures in files you don't own instead of editing them.
- Determinism: seed everything; same seed → byte-identical output.
- No git commits. No real Goodwill data; everything synthetic.
- Final report: files created, how to run, test results (paste the pytest summary line), known gaps, contract changes requested.

## Ownership

| # | Engineer | Owns |
|---|---|---|
| 1 | World | `goodwill_pulse/gen/truth.py`, `goodwill_pulse/sources/ops.py`, `tests/test_truth.py` (may also edit `gen/generate.py`, `gen/world.py`, `gen/writers.py` only to derive the Upright/Cash Monkey report files from the truth world — `tests/test_pipeline.py` must still pass) |
| 2 | Amazon + eBay DBs | `goodwill_pulse/sources/amazon.py`, `goodwill_pulse/sources/ebay.py`, `goodwill_pulse/sources/_common.py`, `tests/test_sources_amazon_ebay.py` |
| 3 | Goodwill marketplace DBs | `goodwill_pulse/sources/shopgoodwill.py`, `goodwillfinds.py`, `goodwillbooks.py`, `tests/test_sources_goodwill.py` |
| 4 | Harmonization SQL | `sql/harmonize/*.sql`, `goodwill_pulse/harmonize.py`, `tests/test_harmonize.py`, `tests/fixtures/` |
| 5 | Data quality + reconciliation | `sql/checks/*.sql`, `goodwill_pulse/quality.py`, `tests/test_quality.py`, `docs/DATA_QUALITY.md` |
| 6 | KPIs | `goodwill_pulse/kpi.py`, `tests/test_kpi.py`, `docs/KPI_DEFINITIONS.md` |
| 7 | Dashboard | `goodwill_pulse/routes/dashboard.py`, `web/dashboard.html`, `tests/test_dashboard_api.py` |
| 8 | Close engine | `goodwill_pulse/sources/finance.py`, `config/close_rules.yaml`, `goodwill_pulse/close/rules.py`, `goodwill_pulse/close/enrich.py`, `tests/test_close_rules.py` |
| 9 | Close outputs | `goodwill_pulse/close/journal.py`, `bc_excel.py`, `invoice.py`, `reconcile.py`, `workbook_compare.py`, `goodwill_pulse/routes/close.py`, `web/close.html`, `tests/test_close_outputs.py` |
| 10 | AI layer | `goodwill_pulse/ai/*.py`, `goodwill_pulse/routes/ai.py`, `tests/test_ai.py` |
| PM | Integration | everything shared, `goodwill_pulse/build.py`, `tests/test_e2e.py` |

`goodwill_pulse/sources/__init__.py`, `goodwill_pulse/close/__init__.py`, `goodwill_pulse/ai/__init__.py` exist and stay empty.

---

## 1. Truth world: `data/sources/_truth.duckdb` (Engineer 1 produces; 2, 3, 8 consume)

The single source of truth every marketplace DB is derived from. Item-first: items are identified at a store →
manifested → listed (maybe relisted) → sold or not. Orders are built from sold items, so every KPI is consistent.

Window: **2025-09-01 .. 2026-10-03 22:00 ET** (13 months + Oct 1–3; YoY for Sept 2026 vs Sept 2025).
Scale: ≈ 160 orders/day total (≈ 104 general merch via Upright: ShopGoodwill 84% / eBay 11% / GoodwillFinds 5%;
≈ 58 books via Cash Monkey: Amazon 70% / eBay 20% / Goodwillbooks 10%), weekday + seasonal shape, slow growth.
Calibrate so **Wed 2026-09-30 Upright paid orders ≈ 128 orders, ≈ $13.2K subtotal** (deck slide 26).
Generation must take < 90 s. Reuse category/price shapes from `gen/world.py`.

All timestamps TIMESTAMPTZ (UTC). Money DECIMAL(12,2). Tables:

| table | columns |
|---|---|
| `stores` | store_id 'Store01'..'Store24', store_name, city, ai_flagging (9 stores), ecom_eye (2 stores) |
| `employees` | employee_id 'E001'.., role (lister/photographer/shipper/manager), hourly_rate, hired_on |
| `items` | item_id (= SKU: `UP-<2-digit store>-<6 digits>` for general merch, `GWM-<store>-<6 digits>` for books), store_id, line_of_business (general_merch/books), category (canonical: Jewelry, Collectibles, Electronics, Clothing, Shoes, Home Decor, Toys & Games, Art, Watches, Books), title, isbn (books), identified_at, flagged_by (ai/person), manifest_id, manifested_at, posted_at (null = backlog), poster_id, list_minutes |
| `listings` | listing_id (globally unique text), item_id, channel (shopgoodwill/ebay/amazon/goodwillfinds/goodwillbooks), tool (upright/cashmonkey), native_listing_id (SGW ItemID / eBay legacyItemId / GF product id / Amazon & GWB = sku), listed_at, ended_at (null = active), status (active/sold/unsold), price, relist_of (listing_id) |
| `buyers` | buyer_id, channel, native_buyer_ref (eBay username / SGW BuyerID / Amazon relay email / GF customer id / GWB customer_ref), state |
| `orders` | order_id (truth id), channel, tool, marketplace_order_id (native format: SGW numeric, eBay '12-34567-89012', Amazon '113-…', GF numeric id + name '#GF…', GWB 'GWB123456'), upright_order_id (Upright orders only), buyer_id, paid_at, item_count, subtotal, shipping_charged, handling (Upright: $3/item; else 0), tax, marketplace_fee, payment_fee, shipping_label_cost, total, payment_type |
| `order_lines` | order_id, line_no, item_id, listing_id, quantity (1), sale_price, fee_alloc |
| `refunds` | refund_id, order_id, refunded_at, amount, reason (≈2–3% of orders; some cross midnight / month end) |
| `payouts` | payout_id, channel, period_start, period_end, paid_on, gross, fees, refunds, net (eBay daily, Amazon every 14 days, SGW per period 1/2/3, GF weekly, GWB monthly paid next month) |
| `shipping_charges` | charge_id, carrier (fedex/osm/pitney_bowes/easypost), order_id, charged_at, amount (positive charge, negative refund/adjustment), tracking |
| `labor` | employee_id, work_date, hours (timeclock) |
| `productivity` | employee_id, work_date, accepted, rejected, photographed, posted |
| `budget` | month, channel, revenue_budget (≈ prior-year actual × 1.08 ± noise) |
| `monthly_inputs` | month, overhead_allocation, store_retail_revenue (e-com ≈ 5% of donated-goods retail) |

Built-in realism the downstream checks rely on (document each in `tests/test_truth.py`):
jewelry high price/low volume; books high volume/low price; AI-flagging stores identify more; backlog growing;
relists ~15% of unsold GM listings; repeat buyers; ~2.5% refunds; the same eBay account sells both tools' items;
minimum online price threshold ($10 GM).

## 2. Marketplace DBs (Engineers 2, 3)

DDL is fixed in `sql/sources/<name>.sql` — create the DB by executing that file, then insert. Reproduce every quirk
listed in the DDL header (text money on eBay, cents on GWB, Pacific naive times on SGW, offsets on GF…).
Each DB must contain exactly the truth orders/listings/refunds/payouts for its channel (eBay = both tools).
Deliberate, documented **dirty data** (≤ 0.5% of rows, seeded, listed in a `_dirty_data` table inside each DB with
columns `table_name, native_key, kind, note`): e.g. a duplicate API row, a SKU with a lower-case prefix, a missing
store code, a test order (`buyer = 'TEST'`), a GF vendor string with a typo. The harmonizer and checks must catch them.
Each module exposes `build(truth_path: Path, out_path: Path, seed: int = 7) -> dict[str,int]` (table → rows).

## 3. Harmonized model: `data/harmonized.duckdb` (Engineer 4 produces; 5, 6, 7, 8, 9, 10 consume)

Schema fixed in `sql/harmonized/schema.sql`. `goodwill_pulse/harmonize.py` exposes
`build(sources_dir: Path, out_path: Path) -> dict` and runs, in order, every `sql/harmonize/NN_*.sql` against a
connection with the source DBs ATTACHed READ_ONLY as `amazon, ebay, shopgoodwill, goodwillfinds, goodwillbooks, ops`.
All transformation logic lives in SQL files (pure DuckDB SQL), not pandas. Must exclude test orders, collapse duplicate
API rows, normalize store/category/channel, compute business_date in America/New_York, allocate fees to lines.

## 4. KPIs (Engineer 6 produces; 7, 10 consume)

`goodwill_pulse/kpi.py`:
```python
KPIS: dict[str, KpiDef]   # registry, key = kpi id e.g. "net_margin", "sell_through", "revenue_per_labor_hour"
@dataclass class KpiDef: id, label, pillar ('growth'|'profitability'|'productivity'|'inventory'|'engagement'),
    unit ('usd'|'pct'|'count'|'days'|'usd_per_hour'), anchor: bool, scorecard: bool, better: 'up'|'down',
    availability: 'built'|'input'|'n/a', source: str (which tables), formula: str (plain English), fn
def compute(con, kpi_id, month: date, store: str|None=None, channel: str|None=None) -> float|None
def kpi_card(con, kpi_id, month, store=None, channel=None) -> dict
    # {id,label,pillar,unit,value,prior_month,prior_year,trend:[{month,value}x13],status:'good'|'watch'|'bad'|None,
    #  availability,source,formula}
def scorecard(con, month, store=None, channel=None) -> dict   # {anchors:[card x3], scorecard:[card x15], pillars:{pillar:[card…]}}
def series(con, kpi_id, months: int=13, store=None, channel=None) -> list[dict]
def connect_harmonized(path=None) -> duckdb.DuckDBPyConnection   # read_only
```
`con` is a connection to harmonized.duckdb. Missing data → `None`, never 0.
Adopted (v1.1): `pct` values are fractions (0.52, not 52); cards carry `scorecard_row`; `series(..., end=month)` is supported.
Open: shipping label cost is not in the harmonized model yet (only eBay SHIPPING_LABEL and finance carrier charges carry it);
net/gross margin exclude it until the PM adds it.

## 5. Month-end close (Engineers 8 → 9)

Engineer 8: `close/rules.py` exposes `run_close(month: date, harmonized_path, finance_path, rules_path) -> CloseRun`:
```python
@dataclass class CloseRun: month, rules_version, allocations: pandas.DataFrame, exceptions: list[dict], source_totals: list[dict]
# allocations columns (one row per journal line, amount > 0 = debit, < 0 = credit; each doc_no sums to 0):
#   doc_no ('ECOM-2026-09-FEDEX'), posting_date, rule_id, source, account_type ('G/L Account'|'Vendor'|'Customer'|'Bank Account'),
#   account_no, department_code, store_id, description, amount, placeholder_account (bool), source_ref (table:key)
# source_totals: [{source, rows, file_total, loaded_total, rules_total}]
# exceptions: [{source, rule_id, severity, message, amount, owner}]
```
Also writes the synthetic **manual allocation workbook** for 2026-09 with exactly one planted re-keying error at
`data/close_inputs/2026-09/E-Commerce Allocation 2026-09.xlsx` (sheet `Journal Entries`, same columns as allocations
minus rule_id/source_ref/placeholder_account) and documents the planted error in `data/close_inputs/2026-09/ANSWER_KEY.md`.
Engineer 9 builds journal/BC Excel/AR invoice/reconciliation/workbook compare from `CloseRun` (build a fake CloseRun
fixture to start; switch to the real one when rules.py lands).

## 6. AI (Engineer 10)

No ANTHROPIC_API_KEY is set right now. Every AI feature must work with a deterministic fallback and switch to Claude
(`claude-opus-5-5`, `anthropic` SDK) when the key exists. Load the `claude-api` skill before writing SDK code.
Narratives are number-checked: every number in generated text must match the facts JSON (tolerance for rounding).

## 7. API routes (PM wires them into api.py; you fill them)

Each `goodwill_pulse/routes/<x>.py` defines `router = APIRouter(prefix="/api/<x>")`. Pages: `web/<x>.html` served at `/<x>`
(full standalone HTML documents with their own `<!doctype html>`; same visual language as `web/index.html`: read its
CSS tokens and copy them; Chart.js from cdnjs only).
