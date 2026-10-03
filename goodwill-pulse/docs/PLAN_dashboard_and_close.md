# Features 2 and 3: Monthly Dashboard and Month-End Close

Plan written Sat Oct 3, 3:25 PM. Builds on what already runs (Feature 1, the Daily Pulse: synthetic reports → intake → canonical `orders` → pulse).
Slide numbers refer to the kickoff deck (Goodwill section, slides 31–42).

```
                         ┌──────────── Feature 1: Daily Pulse (built) ──────────── nightly, revenue + customers
reports ─► intake ─► canonical tables ─┼──────────── Feature 2: Monthly Dashboard ───────── 5 pillars, 15-KPI COO scorecard
                         └──────────── Feature 3: Month-End Close ──────────────── rules ─► BC journal + AR invoice ─► reconcile
```
All three read the same canonical tables. Feature 2 needs **more kinds of data** than orders. Feature 3 needs **more sources and a rules engine**.

---

# FEATURE 2: Monthly E-Commerce Dashboard

> "From manual reporting to management visibility. A monthly E-Commerce dashboard should balance growth, profitability, productivity, inventory management and customer engagement." (slide 32)

## 2.1 What the slides ask for, in one place

The slides describe the same KPIs three ways. We build all three views on one set of KPI functions.

| Slide | Grouping | What it means for the build |
|---|---|---|
| 32 Monthly dashboard | **5 pillars**: Growth · Profitability · Productivity · Inventory · Engagement | The dashboard's navigation |
| 33–34 KPI framework | **5 metric families** (~30 KPIs): Financial · Listing & Production · Sales effectiveness · Category performance · Customer & marketplace | The full KPI catalog (detail views) |
| 35 COO scorecard | **15 KPIs, one page, monthly** in 5 rows: Financial · Productivity · Inventory · Sales · Category + Customer. "Outcomes, operating drivers and early warning indicators." "Trend and target context should be added as data becomes available." | Page 1 of the dashboard |
| 36 2027 plan | **3 anchors**, each "↑ annually": E-com net margin · Revenue per labor hour · Sell-through rate. "Connect profitability, workforce productivity and the speed at which inventory converts to cash." | The top row, with year-over-year trend |
| Debie, pitch | "My daily, my weekly, my monthly and my year to date … budget versus the budget for the day … the month … the year." Every metric **by store** (store gets the credit). | Period switcher, budget vs actual, store filter |

## 2.2 The full KPI catalog: formula, data, pillar, availability

★ = 2027 anchor (slide 36) · **S** = in the 15-KPI COO scorecard (slide 35) · Availability: **Built** (data exists now) · **Add** (needs a synthetic report we add this weekend) · **Input** (monthly manual entry) · **N/A** (no source yet; shown as "not yet available").

### Growth
| KPI | Slide | Formula (our proposal; confirm) | Data | Avail. |
|---|---|---|---|---|
| Total e-commerce revenue **S** | 33, 35 | Σ item subtotal, all channels, month | orders | Built |
| Revenue growth % YoY **S** | 33, 35 | (month − same month last year) ÷ same month last year | orders, 13 months | Add history |
| Revenue by marketplace | 31 | Σ subtotal by channel | orders | Built |
| Budget vs actual (day, month, year) | Debie | actual ÷ budget | budget table | Input |
| E-com share of donated-goods retail | Debie (~5%; peers 10–15%) | e-com revenue ÷ (e-com + store retail) | store sales total | Input |

### Profitability
| KPI | Slide | Formula | Data | Avail. |
|---|---|---|---|---|
| **Net margin %** ★ **S** | 33, 35, 36 | (revenue − marketplace fees − shipping cost − refunds − e-com labor cost − allocated overhead) ÷ revenue | orders, refunds, labor, overhead | Add + Input |
| Gross margin % | 33 | (revenue − fees − shipping cost − refunds) ÷ revenue. Donated goods: no purchase cost | orders, refunds | Add |
| Profit per labor hour | 33 | (gross profit − labor cost) ÷ labor hours | + labor | Add |
| Margin by category | 34 | gross margin % per category | order lines | Add |
| Top 10 categories by margin **S** | 34, 35 | rank categories by gross profit $ | order lines | Add |

### Productivity
| KPI | Slide | Formula | Data | Avail. |
|---|---|---|---|---|
| **Revenue per labor hour** ★ **S** | 33, 35, 36 | e-com revenue ÷ e-com labor hours, same month | orders, labor | Add |
| Listings created **S** | 33, 35 | count of items first listed in month | listings | Add |
| Listings created per day | 33 | listings ÷ working days | listings | Add |
| Listings per employee **S** | 33, 35 | listings ÷ listers (FTE) | listings, labor | Add |
| Sales per employee **S** | 35 | revenue ÷ e-com FTE | orders, labor | Add |
| Average time to list an item | 33 | avg(listed_at − received_in_ecom_at) | items | Add |
| Items identified for e-commerce (by store) | 33 | items flagged (AI or person) | identified items | Add |
| Items sent to e-commerce (by store) | 33 | items on manifests | manifest items | Add |

### Inventory
| KPI | Slide | Formula | Data | Avail. |
|---|---|---|---|---|
| **Sell-through rate** ★ **S** | 34, 35, 36 | items sold within 30 days of listing ÷ items listed, by **listing month cohort** (stores target 50–55%) | listings, order lines | Add |
| Days from donation to listing **S** | 35 | avg(listed_at − identified_at) | items | Add |
| Unlisted inventory backlog **S** | 33, 35 | items sent to e-com and not yet listed, at month end | manifest items, listings | Add |
| Unsold inventory % **S** | 34, 35 | listings ended unsold ÷ listings ended | listings | Add |
| Days to sell | 34 | avg(sold_at − listed_at) | listings, order lines | Add |
| Relisted inventory % | 34 | items listed more than once ÷ items listed | listings | Add |
| Sell-through by category | 34 | as above, by category | | Add |

### Sales and category (slide 34 "Sales effectiveness" + "Category performance")
| KPI | Slide | Formula | Data | Avail. |
|---|---|---|---|---|
| Average selling price **S** | 34, 35 | revenue ÷ units sold | order lines | Add |
| Median sale price | 34 | median item price | order lines | Add |
| Sales / margin / units / ASP by category | 34 | grouped by category | order lines | Add |
| Top 10 categories by revenue **S** | 34, 35 | rank by revenue | order lines | Add |

### Engagement (slide 34 "Customer & marketplace")
| KPI | Slide | Formula | Data | Avail. |
|---|---|---|---|---|
| Number of buyers | 34 | distinct buyer_key in month | orders | Built |
| Repeat buyer rate **S** | 34, 35 | buyers this month with any earlier purchase ÷ buyers this month | orders + history | Built (needs history) |
| New buyers | 34 | buyers whose first purchase is this month | orders | Built |
| Customer satisfaction rating | 34 | marketplace seller rating | eBay/Amazon feedback | N/A (label it) |
| Net Promoter Score "if available" | 34 | survey | none | N/A |
| Marketplace conversion | 34 | sold ÷ viewed or bid | listing views/bids | N/A this weekend |

**Tally:** 15 of 15 scorecard KPIs are computable once we add the item lifecycle, labor and refunds data. Of the ~30 catalog KPIs, 3 are honestly "not yet available" (CSAT, NPS, conversion).

## 2.3 Data we must add (synthetic), and where it would come from in real life

The Daily Pulse only needed orders. The dashboard needs the **item lifecycle**. The Upright menus in slides 22 and 24 show these already exist as exports.

| New synthetic file | Real source it imitates | Grain / key columns | Feeds |
|---|---|---|---|
| `paid_order_items_*.csv` | Upright › Downloads › **Paid order items** (also the month-end input, slide 38) | one row per sold item: Upright order id, item id/SKU, **Supplier (= store)**, category, channel, sale price, final value fee, payment fee, shipping cost | ASP, median, category KPIs, store credit, margin |
| `shopgoodwill_listings_*.csv`, `ebay_listings_*.csv` | Upright › Downloads › **ShopGoodwill listings / eBay listings** | one row per listing: item id, listed_at, list/start price, end_at, status (sold/unsold/active), relist count, lister | listings created, sell-through, unsold %, relisted %, days to sell |
| `manifest_items_*.csv` | Upright › Downloads › **Manifest items** (home screen shows "Unprocessed manifest items 10,059 over 2,913 manifests", slide 21) | item id, store, manifest id, manifested_at, processed (y/n) | items sent, backlog, time to list |
| `operational_productivity_*.csv` | Upright › Reports › **Operational productivity** (slide 22 shows per-user Accepted, Rejected, Photographed, Posted) | user, date, accepted, rejected, photographed, posted | listings per employee, lister output |
| `ecom_identified_*.csv` | Store tablets / AI flagging ("E-Commerce Eye", Thriftly), Debie's pitch | item id, store, identified_at, flagged_by (AI / person), accepted | items identified by store, days donation → listing |
| `timeclock_*.csv` | Payroll / timekeeping export (ask Amanda which system) | employee, role, date, hours, rate | revenue per labor hour, net margin |
| `refunds_*.csv` | Upright › Downloads › **Refunds** | order id, refund date, amount, reason | gross/net margin |
| `budget_2026.xlsx` | Their budget (Debie: "budget for the day / month / year") | month, channel, budget $ | budget vs actual |
| `monthly_inputs.yaml` | Finance (manual, once a month) | overhead allocation $, store retail revenue | net margin, e-com share |

**Generator change:** the synthetic world becomes item-first. Each item is identified at a store → manifested to e-com → listed (by a lister, possibly relisted) → sold or unsold. Orders are built from sold items. That keeps every KPI consistent with every other (sell-through can't contradict revenue). 13 months of history so YoY and cohorts work. Realistic shapes: jewelry high price / low volume; 9 AI-flagging stores identify more; backlog growing (Debie: "we have a big problem with that").

New canonical tables (added to `db.py`): `items`, `listings`, `labor`, `refunds`, `budget`, `monthly_inputs`.

## 2.4 What the dashboard looks like

One page per month, readable in under a minute, filterable by **month · store · channel**, period switch **Day · Week · Month · YTD**.

1. **2027 anchors** (slide 36): three large tiles, each with this month's value, the same month last year, a 13-month line, and an "↑ annually" goal marker.
2. **COO scorecard** (slide 35): the 15 KPIs exactly in the slide's 5×3 grid (Financial / Productivity / Inventory / Sales / Category + Customer). Each cell shows value, vs last month, vs last year and a status chip. Early-warning KPIs (backlog, unsold %, days to list) turn amber or red when they move the wrong way.
3. **Pillar tabs** (slide 32): Growth · Profitability · Productivity · Inventory · Engagement, each with the catalog KPIs above and one or two charts:
   - Growth: revenue by marketplace, 13 months; budget vs actual.
   - Profitability: gross and net margin trend; margin by category (top 10).
   - Productivity: listings per day and per employee; items identified → sent → listed **by store**.
   - Inventory: backlog trend; sell-through by listing cohort and category; days to sell.
   - Engagement: buyers (new vs repeat); CSAT / NPS / conversion marked "not yet available".
4. **Store view**: revenue credited to each originating store and items it sent online (Debie: the store that sends a $50 item that sells for $500 gets the $500).
5. **Data badges** on every KPI: which report it came from, "manual input", or "not yet available". Judges reward this honesty; Amanda can see exactly what's real.
6. **AI, grounded:**
   - "What changed this month": 3–4 sentences, every number checked against the computed KPIs before showing.
   - "Ask the data": questions answered by calling our KPI functions as tools (never free-form SQL), showing which function and filters were used.

Out of scope (Debie, confirmed): recommending how much inventory to move online. We show the reseller balance data (e-com share of donated goods, by store); we don't prescribe it.

## 2.5 Build steps (granular)

| # | Task | Done when |
|---|---|---|
| D1 | Item-lifecycle generator + 7 new report writers in native shapes (2.3) | Files for 13 months written to `data/samples/` |
| D2 | Adapters + mappings for each new report; reuse intake/validation/exceptions | All load; a test per adapter |
| D3 | `kpi.py`: one pure function per KPI, signature `(con, period, store=None, channel=None)` | Unit test per KPI on a tiny hand-built dataset |
| D4 | `GET /api/kpis?month=&store=&channel=` returns anchors, scorecard, pillars, availability | JSON matches the layout |
| D5 | Dashboard tab: anchors, scorecard grid, filters | Renders from API |
| D6 | Pillar tabs with charts (Chart.js from cdnjs) | 5 tabs, ≥1 chart each |
| D7 | Budget, YoY, period switcher | Day/Week/Month/YTD all work |
| D8 | AI narrative + number checker; "Ask the data" with KPI tools | Checker rejects an invented number in a test |

---

# FEATURE 3: Month-End Close → Business Central

> "From manual month-end close to Business Central integration. The current close combines portal downloads, emailed reports, bank activity, spreadsheet rules and manual Business Central entries." (slide 37)
> "The target close automates the rules, not just the downloads." (slide 40)

## 3.1 What happens today (slides 38–39)

**Nine source workflows** (slide 38) → **allocation workbook, 6 manual steps** (slide 39) → Business Central.

| # | Source | Month-end input | Acquisition / rule (verbatim) | How we model it |
|---|---|---|---|---|
| 1 | Cash Monkey | Orders · full month | Submit/download CSV; save as Excel | Same adapter as the pulse, monthly range |
| 2 | Upright | Paid order items · full month | Generate; email delivery; save as Excel | New adapter (also used by the dashboard) |
| 3 | Jewelry | Jewelry Report | Request report; **Co-Pivot populates Supplier** | Enrichment step: fill Supplier (store) by SKU lookup; unmatched → exception |
| 4 | OSM / PB / EasyPost | Shipping amounts | **1st Source acct 0101 • GL 10009** | Bank activity file for account 0101; postage vendor lines → GL 10009 |
| 5 | FedEx | Shipping charges + refunds | **BC GL 40356 • Dept 180 • V00122 • net BNKDEPOSIT refunds** | FedEx charges, minus refunds that arrive as BNKDEPOSIT lines in the bank file |
| 6 | ShopGoodwill | Periodic marketplace reports | **Filter year/month; Period 1 periodic only; Period 3 all reports** | Config rule; **must confirm what Period 1/3 mean** |
| 7 | Goodwill Books | Prior-month payment statement | Monthly email attachment (PDF) | Claude extracts the statement to structured data; lines must sum to its total |
| 8 | eBay | Listing sales report | Seller Center • change date • generate/download | New adapter (eBay Seller Hub columns, documented) |
| 9 | Amazon | Payments summary | Seller Central • request/refresh/download | New adapter (Date Range transaction report columns, documented) |

The workbook today (slide 39): 01 archive inputs · 02 roll last month's workbook forward · 03 type data into **orange cells** · 04 formulas build **Journal Entry tabs** · 05 **copy/paste into a BC General Journal** · 06 create the **AR invoice** from the Invoices tab.

## 3.2 What we build, stage by stage (slide 40's six stages)

| Stage (slide 40) | Our component | Detail |
|---|---|---|
| 01 Acquire: portal reports, email attachments, bank and BC lookups | Intake (exists) + **Close checklist** | For month M, the 9 sources with status: received / missing / needs mapping. File drop now; email pickup and APIs are the Sprint Lab path |
| 02 Archive: consistent year/month naming, run history | Archive (exists) + **close run** record | Files copied to `Month End/{yyyy}/{mm}/Journal Entries/E-Commerce JEs/` (their convention); each close run stored with inputs, rule version, outputs |
| 03 Enrich: supplier assignment, source labels, period metadata | `close/enrich.py` | Supplier (store) on every line; Jewelry Co-Pivot equivalent; label each amount with its source and period |
| 04 Apply rules: monthly range, shipping and refunds, period-specific reports | `close/rules.yaml` + `close/rules.py` | Every workbook rule as data (below); one test per rule |
| 05 Create BC output: General Journal lines, AR invoice entry, control totals | `close/journal.py`, `close/invoice.py`, `close/bc_export.py` | Excel in BC's General Journal layout; AR invoice as BC API JSON + Excel |
| 06 Post + reconcile: import/API status, source-to-BC totals, owned exceptions | `close/reconcile.py` + approval | Source = rules output = journal = posted; exceptions with an owner; approval before export. Stretch: post to a BC sandbox |

**Control principle (slide 40), made testable:** every journal document balances (debits = credits); every line traces to a source file and row; a missing report, a failed rule or a posting error appears in the exceptions list and blocks approval until someone resolves or overrides it with a note.

## 3.3 The rules, written as data

`config/close_rules.yaml` (sketch). Accounts not shown in the deck are **placeholders** and labeled as such.

```yaml
period: monthly                    # first to last day of month, business timezone
sources:
  fedex:
    gl_account: "40356"            # slide 38
    department: "180"              # slide 38
    vendor: "V00122"               # slide 38
    net_refunds_from: bank_0101    # BNKDEPOSIT lines matched to FedEx
  shipping_postage:                # OSM / Pitney Bowes / EasyPost
    bank_account: "0101"           # 1st Source, slide 38
    gl_account: "10009"            # slide 38
  shopgoodwill:
    periods: {1: periodic_only, 3: all_reports}    # slide 38; meaning to confirm
revenue:                           # PLACEHOLDER accounts until Goodwill shares the chart of accounts
  by_channel: {shopgoodwill: "4xxxx-SGW", ebay: "4xxxx-EBAY", amazon: "4xxxx-AMZ", goodwillbooks: "4xxxx-GWB"}
  dimension: store                 # store credit, every revenue line carries the originating store
marketplace_fees: "6xxxx-FEES"
ar_invoice:
  customer_per_marketplace: true   # who the AR invoice is to: confirm
```

## 3.4 Outputs

1. **General Journal (Excel)** in BC's journal layout: Posting Date · Document Type · Document No. · Account Type · Account No. · Description · Department Code · Amount (or Debit/Credit) · Bal. Account. Ready for **Edit in Excel** / journal import, which is the copy-paste step 05 done for them. One document per source, numbered `ECOM-2026-09-FEDEX`, etc.
2. **AR invoice**: BC sales invoice per marketplace customer, as an Excel sheet and as a BC API v2.0 `salesInvoices` + `salesInvoiceLines` JSON payload (replaces step 06).
3. **Reconciliation report** (one page, archived with the run):

   | Source | Rows in file | File total | Loaded | Rules output | Journal | Difference | Status |
   |---|---|---|---|---|---|---|---|
   | FedEx | … | … | … | … | … | 0.00 | ✓ |

4. **Parallel-run check against the workbook** (slide 42 wave 03: "compare every source, total, journal line and invoice to the workbook"). The generator also produces a synthetic "manual" allocation workbook for the month, **with one realistic re-keying error** in an orange cell. Our close output matches every line except that one and flags it. That is the strongest demo moment: the system catches the kind of error Debie described ("every time you re-enter a number … an opportunity for an error").
5. **Exceptions** with an owner and status (slide 41 workstream 5: "define exceptions, approvals, archive and ownership").

## 3.5 Close screen (Month-End tab)

- Month picker, plus a **stage tracker** across the top: Acquire → Archive → Enrich → Apply rules → BC output → Post + reconcile, each green/amber/red.
- **Source checklist**: the 9 sources from slide 38 with received / missing / rule result, and a drop zone.
- **Control totals** table (3.4 #3).
- **Journal preview**: lines grouped by document, with balance check.
- **Workbook comparison**: matches, and differences highlighted.
- **Approve and export** button (disabled while blocking exceptions are open) → downloads the journal Excel, the invoice and the reconciliation report.
- **AI help**: extract the Goodwill Books PDF statement; explain each reconciliation break in plain English (e.g. "FedEx refund of $42.10 is dated Oct 1, outside September").

## 3.6 Build steps (granular)

| # | Task | Done when |
|---|---|---|
| C1 | Writers for bank_0101, FedEx, eBay listing sales, Amazon payments summary, ShopGoodwill periodic, Jewelry, Goodwill Books PDF | Sample month written |
| C2 | Adapters/mappings for each (reuse intake, archive, exceptions) | All load; tests |
| C3 | `close_rules.yaml` + rules engine; FedEx netting, shipping 0101→10009, ShopGoodwill periods, monthly range | One unit test per rule |
| C4 | Journal builder + balance check + BC Excel writer (openpyxl) | Balanced file; opens in Excel |
| C5 | AR invoice builder (Excel + BC API JSON) | Payload validates against BC field names |
| C6 | Reconciliation + workbook comparison (synthetic workbook with one planted error) | Error found and explained |
| C7 | Month-End tab UI (3.5) + approve/export | One-click close for September 2026 |
| C8 | Goodwill Books PDF extraction with Claude; lines must sum to the statement total | Works on the sample PDF |
| C9 (stretch) | Post to a Business Central sandbox (API v2.0 `journals/journalLines` + `Microsoft.NAV.post`) | Posting response captured |

---

# Order of work and team split

**Priority:** the demo needs one full run of each feature, not every KPI. If time runs short, cut from the bottom of each list (D7–D8, C8–C9) and keep the anchors, the scorecard and a balanced, reconciled journal.

| When | Data + generator | Feature 2 (dashboard) | Feature 3 (close) | Demo / pitch |
|---|---|---|---|---|
| Sat 3:30–4:15 | Item-lifecycle world (D1) | KPI definitions locked (2.2) | `close_rules.yaml` from slide 38 (C3 sketch) | Questions for Amanda (below) |
| 4:15–5:30 | *Amanda (4:15) and Michael (5:00): confirm definitions, Period 1/3, GL accounts, AR customer* ||||
| 5:30–9:00 | Writers C1 + D1 | D2–D5 (anchors + scorecard live) | C2–C4 (journal balances) | Script draft |
| Sun 10:00–1:00 | Messy month + planted workbook error | D6–D7 | C5–C7 | Slides |
| Sun 1:00 | **Feature freeze.** Record the full demo by 1:30 PM ||||
| Sun 1:30–3:30 | — | D8 if time | C8 if time | Re-record, submit by 3:30 |

# Questions that decide numbers (bring to Amanda at 4:15)

1. Sell-through: sold within how many days of listing, or ever? By listing month?
2. Net margin: which costs go in (labor, overhead allocation, shipping supplies)? Who provides overhead?
3. Where do e-com labor hours come from?
4. Does Upright record when an item was identified at the store, or only when manifested?
5. ShopGoodwill "Period 1 periodic only; Period 3 all reports": what are the periods?
6. Revenue, fee and receivable GL accounts and dimensions the workbook posts to (beyond 10009 and 40356/Dept 180)?
7. Who is the AR invoice customer: each marketplace, or one?
8. What does "Co-Pivot populates Supplier" do in the Jewelry report?
9. Can we see a redacted copy of the allocation workbook's tab names and orange cells?
10. Is there a monthly e-com budget by channel?
