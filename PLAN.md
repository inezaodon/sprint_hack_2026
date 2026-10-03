# Goodwill Michiana: Reports → Daily Pulse, Monthly Dashboard, Business Central Close

Working plan for SprintHack@ND, Goodwill track. Written Sat Oct 3, 3:00 PM. Code freeze Sun Oct 4, 4:00 PM.

Sources used: kickoff deck (slides 18–42 and the report-step screenshots inside them), the team Google Doc (all 9 tabs: Problem & Build Plan, Overall Notes, Key Timelines, Granola Notes, both intro transcripts, Demo requirements, Feature development overview, Automating report processing), and public documentation for eBay, Amazon, Upright Labs, Cash Monkey and Business Central.

---

## 0. The problem in one paragraph

Goodwill Michiana runs 24 stores. Items flagged for e-commerce are listed online through two tools: **Upright** (Upright Labs' "Lister": general merchandise to ShopGoodwill, eBay and GoodwillFinds) and **Cash Monkey** (books and media to Amazon, eBay and Goodwillbooks). Every morning a manager or assistant manager logs into each tool, generates a report, downloads it, counts rows, and types totals into a Daily Summary Spreadsheet (30–60 min/day). Nobody does it on weekends, so Debie sees Friday–Sunday e-commerce on Monday, while store sales reach her at **10 PM every night**. At month-end, accounting gathers 9 source reports, types them into the orange cells of a copied-forward allocation workbook, and pastes the resulting journal entries into Business Central. Every re-keyed number is an error risk.

**What we build:** a system that takes the reports *as they already exist*, translates them into one clean data model, and produces three outputs from it:

1. **Daily Pulse**: a nightly summary of the reports that came in for that business date: revenue and customer count by marketplace, then enterprise totals. Delivered at 10 PM, including weekends.
2. **Monthly E-Commerce Dashboard**: Growth, Profitability, Productivity, Inventory, Engagement. One page, sliceable by store, month, channel.
3. **Month-End Close Package**: Business Central–ready General Journal lines and AR invoice, with control totals, reconciliation and an exceptions list.

Debie's own framing of the boundary: *"I'm just asking for good, clear, easy to find data so we can make those decisions."* We show data; we do not recommend how much inventory to move online.

---

## 1. Facts we now know (and where each came from)

| # | Fact | Source |
|---|---|---|
| F1 | Store sales arrive at 10 PM nightly; e-commerce for Fri–Sun arrives Monday | Debie, pitch transcript |
| F2 | One account per platform for the whole organization, not per store | Debie, transcript |
| F3 | When an item sells online, the **originating store gets the revenue credit**. Every metric must slice by store | Debie, transcript |
| F4 | Upright's dashboard shows "Weekly Supplier Sales" by **Store09, Store10…**: in Upright, **supplier = store** | Slide 21 screenshot |
| F5 | Upright report menu: Operational productivity, Poster overview, Poster targets, Manifests, Suppliers, Top sales, Event logs, Sales by category. Downloads: GoodwillFinds listings, ShopGoodwill listings, eBay listings, **Paid orders**, Paid order items, Orders, Refunds, Manifest items, Shipments, Products, Embedded listings | Slides 22, 24 screenshots |
| F6 | Upright "Paid orders" = all orders *paid* within a date range, filterable by channel and refund status. **Timezone selector, "Use America/Los_Angeles for SGW"**. The report is generated asynchronously and **emailed** when ready | Slide 24 |
| F7 | Upright Paid orders columns (A→R visible): Upright Order ID, Channel, Channel Order ID, Secondary Channel, Channel Buyer, Order Item Count, Payment Method, Payment Type, Total, Subtotal, Shipping Total, Shipping Discount, Handling, Tax Total, Donation, Currency, Final Value Fee, Payment Fee (more columns cut off) | Slide 26 screenshot |
| F8 | The Upright export ends with a **totals row** (`=SUM(J2:J129)`): one day ≈ 128 orders, $13,247 subtotal, $1,452 shipping (for 9/30/2026). Customer count today = rows − title row, which is effectively **paid orders**, not unique buyers | Slide 26 |
| F9 | Cash Monkey Orders report: "orders across all marketplaces, with profit information where available. **One line per unit!**" Shipping and market fees pro-rated per unit. Dates are **UTC**, inclusive. Accounts **276 – Goodwill Michiana** and **277 – Goodwill Michiana (Stores)**. Channels **Amazon-MF, eBay, Goodwillbooks**. CSV filename like `orders2023-20261001-132256-96170.csv` | Slides 28–30 |
| F10 | Cash Monkey also has an "Orders Marketplace Summary (Daily) (Beta)": one day rolled up by market | Slide 28 |
| F11 | AI flagging is live in 9 of 24 stores; "E-Commerce Eye" in 2. Minimum price threshold below which items don't go online | Debie, transcript |
| F12 | Month-end: 9 sources, allocation workbook (6 steps), GL 10009 (shipping from 1st Source acct 0101), GL 40356 / Dept 180 / Vendor V00122 (FedEx, net BNKDEPOSIT refunds), ShopGoodwill Period 1 = periodic only / Period 3 = all reports | Slides 37–42 |
| F13 | Hackathon data: **synthetic or public data only**; real data comes in Innovation Sprint Lab | Organizers + Debie |
| F14 | Constraint judges score for Goodwill: **"tools they already pay for"** (Business Central, Microsoft) | Rubric |
| F15 | Team meetings: Amanda Baumer 4:15–4:45 PM (Huddle Room); Michael Wicks 5:00–5:30 PM (Room 109); Ryan Geraghty (Google) Sun 3:00 PM | Doc, Key Timelines tab |

> **Correction to the whiteboard model** (CM + A + eBay + SG → 1 big report): there are really **two** daily reports, not four. Upright already combines ShopGoodwill + eBay + GoodwillFinds; Cash Monkey already combines Amazon + eBay (books) + Goodwillbooks.
>
> **Stack decision (Sat 3:10 PM):** Python + FastAPI backend (translation engine), one JS dashboard served live by FastAPI and also publishable as a claude.ai artifact (AI via the artifact's built-in Claude access until an API key is set up). Built so far: `goodwill-pulse/` (see its README).

### Consequences that shape the design

- **Two systems cover almost everything.** Upright (ShopGoodwill + eBay + GoodwillFinds) and Cash Monkey (Amazon + eBay books + Goodwillbooks) are the daily inputs. Amazon, eBay and ShopGoodwill native reports matter mostly at month-end (fees, payouts).
- **eBay appears in both systems** (general merchandise via Upright, books via Cash Monkey). The pulse's eBay row must combine them, and the two must never double-count.
- **Each source counts a "customer" differently.** Upright: one row per paid order. Cash Monkey: one row per *unit*, so we must count distinct order IDs. Neither gives unique people across channels.
- **Each source has a different day boundary.** ShopGoodwill runs on Pacific time (F6), Cash Monkey on UTC (F9), Goodwill Michiana on Eastern. A sale at 10:30 PM Eastern lands on different "days" in each report. We define one **business date** in America/New_York (to match the 10 PM store report) and convert every timestamp.
- **Upright already knows store, category, lister and listing dates.** Productivity and inventory KPIs are realistic to source from Upright reports (Poster overview, Manifest items, Listings), not just guesses.

---

## 2. Where we get the reports

We are building as if the reports exist. They exist in four layers, from most real to most synthetic:

### 2a. Ask Goodwill for real, redacted exports (4:15 PM with Amanda)

The single highest-value ask. Even one redacted file per source locks our adapters to the true columns. Ask for: one Upright *Paid orders* CSV, one Cash Monkey *Orders* CSV, the list of tabs/orange cells in the E-Commerce Allocation workbook, and the BC journal template they paste into. If they can't share, ask them to confirm our column lists below.

### 2b. Exact report shapes from public documentation and the deck

| Report | Shape we will reproduce | Evidence |
|---|---|---|
| Upright Paid orders | Columns in F7, one row per order, trailing SUM row, filename `paid_orders_MM-DD-YYYY_MM-DD-YYYY.csv` | Slide 26 screenshot |
| Upright Paid order items | One row per sold item: item/SKU, supplier (store), category, sale price, fees, channel | Upright help center describes it as "a downloadable .csv history of every sold item on your marketplaces" with channel and fees (full column list is behind Upright's login) |
| Cash Monkey Orders | One row per unit, UTC dates, account 276/277, channel Amazon-MF / eBay / Goodwillbooks, net revenue and profit, pro-rated fees and shipping | Slides 28–30 |
| eBay Seller Hub Orders report | Documented CSV: Sales Record Number (col 1), Order Number, Buyer Username, … Sold For (col 28), Shipping And Handling (col 29), … Total Price (col 46) | eBay help, Seller Hub orders report |
| Amazon Date Range Transaction report | date/time, settlement id, type, order id, sku, description, quantity, marketplace, fulfillment, order city/state/postal, tax collection model, product sales, product sales tax, shipping credits, shipping credits tax, gift wrap credits, promotional rebates, marketplace withheld tax, selling fees, fba fees, other transaction fees, other, total (+ Transaction Release Date for 2025+) | Amazon SP-API payment report docs and third-party guides |
| Bank activity (1st Source acct 0101) | Date, description, amount, type: lines from OSM / Pitney Bowes / EasyPost and FedEx, BNKDEPOSIT refunds | Slide 38 rules |
| Goodwill Books statement | Monthly emailed PDF (prior-month payment statement) | Slide 38 |

### 2c. Public data to make the synthetic reports realistic

| Dataset | What it gives us | Use |
|---|---|---|
| **Mercari Price Suggestion** (Kaggle, ~1.4M secondhand listings: name, condition, category, brand, price, shipping) | Real price distributions for used goods by category | Item titles, categories and price shapes |
| **UCI Online Retail II** (~1M transactions, customer IDs, dates) | Realistic repeat-buyer and weekday/seasonal patterns | Buyer behavior for repeat-buyer rate and weekly cycles |
| **ShopGoodwill public listings** (the buyer site's search is public) | Real Goodwill category names and auction price ranges | Optional: a small reference sample only. Check the site's terms first; don't scrape at volume |

### 2d. Our generator writes the actual report files

A seeded Python generator creates ~13 months of activity for 24 stores, then **writes it out in each source's native shape**: different column names, date formats, time zones, a totals row, one-row-per-unit for Cash Monkey, refunds as separate rows, and so on. The adapters then have real work to do, which is the point. It also produces **messy variants** for the demo: a duplicate order, a refund crossing midnight, a missing supplier/store, a renamed column, a missing report.

---

## 3. System architecture

```
 REPORTS (as Goodwill gets them today)            TRANSLATION LAYER                      OUTPUTS
 ────────────────────────────────────            ─────────────────                      ───────
 Upright: Paid orders (email/CSV) ──┐
 Upright: Paid order items ─────────┤   1 Intake    → inbox folder / email attachment     Daily Pulse  (10 PM, every day)
 Upright: Listings, Manifest items, │   2 Recognize → which report is this? (rules, then   ─ revenue + customers by channel
   Poster overview, Refunds ────────┤                 Claude if unknown)                    ─ enterprise total, vs last week
 Cash Monkey: Orders (UTC, per unit)┤   3 Archive   → raw file kept, hashed, logged        ─ data-freshness per source
 eBay Seller Hub: Orders ───────────┤   4 Adapt     → source columns → canonical schema    ─ Claude narrative (verified)
 Amazon: Date Range transactions ───┤   5 Validate  → types, dupes, totals row, store ID
 ShopGoodwill: periodic reports ────┤   6 Load      → DuckDB: orders, order_lines,       Monthly Dashboard
 Bank: 1st Source acct 0101 ────────┤                 items, listings, labor, refunds,     ─ 5 pillars, 15 COO KPIs
 FedEx charges / refunds ───────────┤                 payouts, bank_lines, stores          ─ filter: month, store, channel
 Goodwill Books statement (PDF) ────┤   7 Exceptions → anything that failed, with reason   ─ "Ask the data" chat (Claude)
 Jewelry report ────────────────────┘
                                                                                         Month-End Close
                                                                                          ─ rules engine (workbook as code)
                                                                                          ─ BC General Journal (Excel)
                                                                                          ─ AR invoice payload
                                                                                          ─ reconciliation + exceptions
```

One principle runs through all of it: **deterministic code computes every number; Claude reads, maps, explains and answers, and its outputs are checked against the computed numbers.** A finance team will only trust it if that line holds.

---

## 4. The canonical data model

Everything downstream reads these tables. This is the contract the whole team builds against; lock it first.

### `orders`: one row per order per channel
| column | type | notes |
|---|---|---|
| order_key | text PK | `{source}:{channel}:{channel_order_id}` (prevents double counts across reloads and across Upright/Cash Monkey) |
| source_system | enum | upright, cashmonkey, ebay_native, amazon_native, shopgoodwill_native |
| channel | enum | shopgoodwill, ebay, amazon, goodwillfinds, goodwillbooks, other (config-driven) |
| line_of_business | enum | general_merch, books |
| channel_order_id | text | |
| buyer_key | text | hash of channel + buyer username/ID (never store names) |
| paid_at_utc | timestamp | normalized from source time zone |
| business_date | date | paid_at converted to America/New_York |
| item_count | int | |
| subtotal | decimal | item revenue |
| shipping_charged | decimal | what the buyer paid for shipping |
| handling | decimal | |
| tax | decimal | collected; not revenue |
| marketplace_fees | decimal | final value + payment + referral fees |
| refund_amount | decimal | 0 unless refunded |
| total | decimal | |
| source_file_id, source_row | fk, int | traceability back to the raw file |

### `order_lines`: one row per unit sold
`order_key, item_id, sku, store_id, category, sale_price, fee_alloc, shipping_alloc, profit (if source gives it)`

### `items`: item lifecycle (the inventory and productivity backbone)
`item_id, store_id, category, identified_at, sent_to_ecom_at, listed_at, lister_id, list_minutes, list_price, channel, status (backlog|listed|sold|unsold|relisted|returned_to_store), sold_at, relist_count`

### `stores`
`store_id (Store01–Store24), name, ai_flagging (bool), ecom_eye (bool)`

### `labor`
`employee_id, role (lister, photographer, shipper, manager), date, hours, hourly_cost`

### `bank_lines`, `payouts`, `fedex_lines`, `refunds`
Month-end inputs, each with `source_file_id`.

### `budget`
`date, channel, revenue_target` (synthetic; lets us show budget vs actual like her store report)

### `report_files` (the archive and run log)
`file_id, source_type, original_name, sha256, received_at, period_start, period_end, row_count, control_total, status (loaded|partial|rejected), mapping_version`

### `exceptions`
`exception_id, file_id, row, rule, severity, message, suggested_fix, status (open|resolved|ignored), owner`

### Config, not code
- `channels.yaml`: the pulse rows and which source/channel values roll into each. "Other marketplaces can be added as separate rows" becomes one line of config.
- `mappings/*.yaml`: one per report type: column name → canonical field, date format, time zone, header/footer rules.
- `gl_map.yaml`: source + amount type → GL account, department, debit/credit, vendor/customer.

---

## 5. Feature breakdown (granular)

Priority: **P0** = in the recorded demo no matter what. **P1** = planned. **P2** = only if ahead.

### EPIC A: Report intake and translation (the foundation)

**A1. Intake (P0)**
- A1.1 Watched folder `inbox/` (stands in for a OneDrive/SharePoint folder or a shared mailbox). Dropping files triggers a run.
- A1.2 Upload button in the dashboard as a second path.
- A1.3 (P2) Read attachments from a mailbox folder, since Upright and Goodwill Books both arrive by email. Production path: Power Automate rule saves attachments to the SharePoint folder.

**A2. Recognize the report type (P0)**
- A2.1 Fingerprint by header set + filename pattern (`paid_orders_*`, `orders2023-*`). Deterministic, instant, free.
- A2.2 If no fingerprint matches, call Claude (see C1) with the header row and 5 sample rows → `{report_type, confidence, column_mapping}`. Show it to the user to confirm; save as a new mapping version. Never auto-load an unconfirmed mapping.

**A3. Archive (P0)**
- A3.1 Copy raw file to `archive/{yyyy}/{mm}/{source}/` (mirrors their `Accounting / Month End / year / month / Journal Entries / E-Commerce JEs` convention). Store sha256; a re-dropped identical file is a no-op.
- A3.2 Write a `report_files` row with row count and control total.

**A4. Adapters (P0)**: one function per report type, all producing canonical rows.
- A4.1 Upright Paid orders → `orders`. Drop the header and the **SUM totals row** (detect: blank ID column + numeric sums). Convert Pacific → UTC → business_date.
- A4.2 Cash Monkey Orders → `order_lines`, then **group by order ID** into `orders`. Convert UTC → business_date. Map Amazon-MF → amazon, eBay → ebay (line_of_business = books), Goodwillbooks → goodwillbooks.
- A4.3 Upright Paid order items → `order_lines` with store_id (= supplier) and category.
- A4.4 (P1) Upright listings / manifest items / poster overview → `items`, `labor`-like lister output.
- A4.5 (P1) Amazon Date Range transactions, eBay Orders, bank lines, FedEx → month-end tables.
- A4.6 (P2) Goodwill Books PDF statement → Claude extraction (C2).

**A5. Validate (P0)**: each failure becomes an `exceptions` row; good rows still load.
- duplicate order_key · missing/unknown store · negative or non-numeric amounts · date outside the report's stated range · refund without matching order · unknown channel value · column missing or renamed · file row total ≠ footer total.

**A6. Freshness check (P0)**
- For each business date and each expected source: received / late / missing. The pulse prints this so a missing report is never shown as $0.

### EPIC B: Daily Pulse

**B1. Business-date rollup (P0)**
- Revenue = Σ subtotal (decide with Amanda: subtotal vs subtotal + shipping; we show subtotal and note it) by pulse row.
- Customers = count of distinct orders per channel (matches today's "rows minus title row"); also compute distinct buyers as a secondary number.
- Enterprise total row. Total customers = sum of channel orders (a buyer on two channels counts twice; say so in a footnote).

**B2. Pulse layout (P0)**: exactly the slide's table:

| Revenue source | Daily revenue | Daily customers |
|---|---|---|
| ShopGoodwill | | |
| Amazon | | |
| eBay | | |
| Other e-commerce channels | | |
| **Total e-commerce** | | |

plus: vs same day last week, month-to-date vs budget, data freshness per source, open exceptions count.

**B3. Delivery (P0 HTML, P1 email)**: HTML email/page. Production: Outlook/Teams via Power Automate.

**B4. Schedule (P1)**: runs at 10 PM ET every day, including Sat/Sun. Demo shows a Saturday pulse produced with nobody at work.

**B5. Narrative line (P1, Claude)**: e.g. "Saturday revenue $14.2K, up 18% vs last Saturday, led by ShopGoodwill jewelry. Cash Monkey report not received yet." Every number in it is checked against B1's figures (C3).

### EPIC C: Claude API layer (where AI does work code can't)

All calls use the official `anthropic` Python SDK, model `claude-opus-5-5`, structured outputs where a shape is expected, prompt caching on the stable system prompt and schemas.

**C1. Report recognizer and column mapper (P0)**
- Input: header row, 5 sample rows, filename, list of known report types and the canonical schema.
- Output (structured): `report_type`, `confidence`, per-column `{source_column, canonical_field | ignore, transform: date_format/timezone/sign}`, `notes`.
- Guardrail: the mapping is validated by running the adapter on the sample and checking types and totals before a human confirms it. This is how "a renamed column" becomes a 10-second fix instead of a broken report.

**C2. Document extractor (P1)**
- Input: an emailed PDF (Goodwill Books payment statement; FedEx invoice).
- Output (structured): statement period, line items, gross, fees, net payout.
- Guardrail: lines must sum to the stated total, or the file goes to exceptions.

**C3. Grounded narrative writer (P1)**
- Input: the computed pulse/dashboard facts as JSON.
- Output: 2–4 sentences.
- Guardrail: a checker extracts every number from the text and requires it to appear in the facts JSON (with rounding tolerance). Fails → regenerate once → otherwise publish without narrative. This makes "the AI doesn't invent numbers" provable on stage.

**C4. "Ask the data" chat (P1)**
- Tool use over a fixed set of query functions (`revenue(period, channel, store)`, `top_categories(period, by)`, `sell_through(cohort, store)`, `exceptions(open)`…), never free-form SQL.
- Answers cite which function and filters were used. "Which stores sent the most to e-commerce last month?" is answered from the data model.

**C5. Close exception explainer (P2)**
- For each reconciliation break, a plain-English explanation and a likely cause ("FedEx refund of $42.10 posted to BNKDEPOSIT on Oct 1, outside the September range").

### EPIC D: Monthly E-Commerce Dashboard

Layout: top row = the three 2027 anchors (net margin, revenue per labor hour, sell-through), each with trend and year-over-year. Then five pillar sections. One filter bar: month, store, channel. Views: day / week / month / YTD.

| Pillar | KPIs (bold = in the 15-KPI COO scorecard) | Formula | Data |
|---|---|---|---|
| **Growth** | **Total e-com revenue**; **revenue growth % YoY**; revenue by channel; budget vs actual; e-com share of donated-goods retail | Σ subtotal; (this month − same month LY) ÷ LY | orders, budget |
| **Profitability** | **Net margin %** ★; gross margin %; profit per labor hour; **top 10 categories by margin** | gross = (sales − fees − shipping cost − refunds) ÷ sales; net = (gross profit − e-com labor cost − allocated overhead) ÷ revenue | orders, order_lines, labor |
| **Productivity** | **Listings created**; **revenue per labor hour** ★; **listings per employee**; **sales per employee**; avg time to list; items identified / sent by store | revenue ÷ labor hours; listings ÷ listers | items, labor (Upright Poster overview) |
| **Inventory** | **Days from donation to listing**; **unlisted inventory backlog**; **unsold inventory %**; **sell-through rate** ★; days to sell; relisted %; **ASP**; median price; **top 10 categories by revenue** | sell-through = sold ÷ listed, by listing cohort; backlog = sent − listed | items, order_lines |
| **Engagement** | **Repeat buyer rate**; buyers; new buyers; refund/return rate; CSAT/NPS shown as "not yet available" | buyers in period with a prior purchase ÷ buyers in period | orders (buyer_key) |

★ = 2027 anchor. Stores target 50–55% sell-through; the e-com target is unknown and the dashboard is how they'll find it.

D-tasks: D1 KPI functions (pure, unit-tested) · D2 anchor tiles · D3 pillar sections · D4 store/channel/month filter · D5 store-credit view (revenue credited to originating store) · D6 drill-down: top 10 categories · D7 YoY and budget overlays · D8 "Ask the data" panel (C4).

### EPIC E: Month-End Close → Business Central

Goodwill's target close, stage by stage, and what we build for each:

| Stage (their words) | What we build | P |
|---|---|---|
| 01 Acquire | Intake for the 9 sources (A1); month view shows 9/9 received | P0 for 3 sources, P1 rest |
| 02 Archive | `archive/` + run history (A3) | P0 |
| 03 Enrich | store/supplier on every line; Jewelry supplier fill; period metadata | P1 |
| 04 Apply rules | `rules/` engine: monthly date range; ShopGoodwill Period 1 vs Period 3; shipping (0101 → GL 10009); FedEx (GL 40356, Dept 180, V00122, net BNKDEPOSIT refunds) | P0 for FedEx + shipping + Upright sales |
| 05 Create BC output | General Journal Excel in BC's Edit-in-Excel layout + AR invoice | P0 journal, P1 invoice |
| 06 Post + reconcile | Control totals: source = rules output = journal; balanced check; exceptions list. (P2: post to a BC sandbox via the v2.0 `journals/journalLines` API + `Microsoft.NAV.post`) | P0 reconcile, P2 API |

E-tasks:
- E1 `gl_map.yaml`: the codes from the deck, plus clearly labeled **placeholder** accounts for revenue, marketplace fees and AR (we don't know their chart of accounts; say so).
- E2 Rules as data with one unit test per rule (known input → expected journal lines).
- E3 Journal builder: groups by source/period → lines {Posting Date, Document Type, Document No., Account Type, Account No., Description, Department Code, Debit, Credit}; enforces debit = credit per document.
- E4 Excel writer (openpyxl) in BC's General Journal layout.
- E5 AR invoice payload: customer = marketplace (e.g. ShopGoodwill), lines by revenue type.
- E6 Reconciliation report: per source, raw total → loaded total → journal total, with differences and reasons.
- E7 Payout matching (P1): orders → marketplace payout → bank deposit, with fee netting; unmatched items to exceptions.

### EPIC F: Synthetic data generator (feeds everything)

- F1 Entities: 24 stores with uneven volume; 9 AI-flagging stores list more; ~30 listers with differing speeds.
- F2 Items: categories from Mercari with real price shapes (jewelry high price/low volume, books high volume/low price); minimum online price threshold.
- F3 Lifecycle: identified → sent → listed (lag days by store) → sold/unsold/relisted.
- F4 Orders: weekend and seasonal patterns, repeat buyers, refunds (~2–3%), ShopGoodwill auctions ending at Pacific times.
- F5 Writers: one per report type, in that report's native columns, date format, time zone and quirks (totals row, one line per unit).
- F6 Messy scenario pack: duplicate order, renamed column, missing store, refund crossing midnight, missing Cash Monkey file, FedEx refund in the next month.
- F7 13 months of history (YoY works) + a budget table.

### EPIC G: Demo, pitch and submission

- G1 Demo script (≤3 min): problem in Debie's words → drop Saturday's reports → pulse appears → dashboard (anchors, filter one store) → messy re-run → close package reconciles → what's real vs synthetic.
- G2 Recorded video embedded in Google Slides ("anyone with the link").
- G3 GitHub repo, README, built-vs-used list (libraries, Claude API, datasets, AI coding assistance).
- G4 Submit by 3:30 PM Sunday; resubmit is allowed.

---

## 6. Technology choices (proposed)

| Need | Choice | Why |
|---|---|---|
| Language | Python 3.11+ (system has 3.9; use a venv via `uv` or Homebrew Python) | pandas/DuckDB fit; current `anthropic` SDK needs a modern Python |
| Storage | DuckDB file | Zero setup; SQL over CSVs; fast |
| Transform | pandas | Familiar; Excel/CSV IO |
| Dashboard + pulse UI | Streamlit (installed) | One person can build it in hours; filters and tables out of the box |
| AI | `anthropic` SDK, `claude-opus-5-5`, structured outputs, tool use, prompt caching | C1–C5 |
| Excel output | openpyxl (installed) | BC journal file |
| Scheduling | APScheduler or cron in demo; Power Automate in production | 10 PM pulse |
| Tests | pytest | KPI functions and rules must have tests; judges will ask |

**Production story for judges ("tools they already pay for"):** inputs arrive as today (email/download → SharePoint folder via Power Automate), outputs land in Outlook/Teams, Excel and Business Central (Edit-in-Excel import now, BC API later). The Python service is the translation engine; nothing new for staff to buy or learn.

### Repo layout
```
goodwill-pulse/
  config/  channels.yaml  gl_map.yaml  mappings/*.yaml
  gen/     generate.py  writers/*.py  scenarios/*.py
  ingest/  intake.py  recognize.py  adapters/*.py  validate.py  load.py
  ai/      client.py  mapper.py  extractor.py  narrator.py  chat_tools.py
  kpi/     metrics.py  (pure functions)
  pulse/   build.py  render.py  schedule.py
  close/   rules.py  journal.py  bc_excel.py  reconcile.py
  app/     streamlit_app.py  (Pulse | Dashboard | Month-End | Exceptions | Ask)
  tests/
  data/    inbox/  archive/  out/  warehouse.duckdb
```

---

## 7. Build order and team split

The schema (§4) and the KPI definitions (§5 D) are tonight's one non-negotiable. Everything parallelizes from there.

| Time | Data + generator | Translation (A) | Pulse + dashboard (B, D) | Close (E) | AI (C) + pitch |
|---|---|---|---|---|---|
| Sat 3:00–4:15 | Generator skeleton, store/item/order tables | Canonical schema + DuckDB | Streamlit shell | gl_map from deck | Questions for Amanda; slide skeleton |
| 4:15–5:30 | *Amanda + Michael meetings: confirm columns, customer definition, delivery, BC template* |||||
| 5:30–9:00 | Upright + Cash Monkey writers, messy pack | Upright + Cash Monkey adapters, validation, exceptions | Pulse table end-to-end | Rules + journal for FedEx/shipping/Upright sales | C1 mapper; C3 narrator + number check |
| **Sat 9:00 checkpoint** | **drop files → pulse renders from real adapters** |||||
| Sun 10:00–1:00 | Listings/labor data | eBay/Amazon/bank adapters | Dashboard pillars + anchors + filters | BC Excel + reconciliation | C4 chat; demo script |
| **Sun 1:00** | **Feature freeze. If the pipeline isn't solid, cut C4 and E-invoice; protect the recording** |||||
| Sun 1:00–3:30 | Record demo (first full take by 1:30), slides, README, built-vs-used, submit |||||

---

## 8. Definition of done (maps to the rubric)

| Rubric (weight) | Our evidence |
|---|---|
| Working evidence (26) | One take: drop Saturday's reports → pulse → dashboard → close package. Second run with the messy pack; exceptions caught; totals still reconcile |
| Partner problem fit (22) | Before: 6 + 4 manual steps per platform, weekends on Monday. After: 0 clicks, 10 PM every night |
| Fits constraints (19) | Inputs are their exact reports; outputs are their pulse table, Excel and a BC journal; Microsoft production path |
| Technical substance (15) | Adapters, time-zone business dates, KPI tests, rules engine, number-checked AI; every owner can explain their module |
| Demo clarity (11) | First 30 s: "Debie gets store sales at 10 PM but e-commerce on Monday. Now she gets both at 10 PM." |
| X-factor (7) | Sprint Lab path: first real connector (Upright email), parallel run against last month's workbook |

---

## 9. Questions for Amanda (4:15) and Michael (5:00)

1. Daily customers: paid orders (today's row count) or unique buyers?
2. Daily revenue: subtotal only, or including shipping charged?
3. Which time zone defines "the day" for the pulse: Eastern, to match the 10 PM store report?
4. Which channels make up "Other": GoodwillFinds, Goodwillbooks, anything else?
5. Can you share one redacted Upright Paid orders file and one Cash Monkey Orders file?
6. Is Supplier in Upright always the originating store?
7. Where do e-com labor hours live (timeclock, scheduling)? Needed for revenue per labor hour.
8. Which BC accounts/dimensions does the allocation workbook post to besides 10009 and 40356/Dept 180? Who is the AR invoice customer?
9. What Microsoft licenses do you have beyond Business Central (M365, Power Automate, Power BI, Teams)?
10. Who reads the pulse, and where: email, phone, Teams?
11. Does Upright track when an item was identified/received, for days from donation to listing?
12. Are in-store sales in scope at all? (Our assumption: no.)

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Real column names differ from our reconstruction | Mappings are config + C1 remaps a new header in seconds; ask Amanda for samples |
| Scope across 30+ KPIs | Ship the 3 anchors + 15 scorecard KPIs; list the rest as "same model, next sprint" |
| BC details unknown | Placeholder accounts labeled as such; the rules/mapping design is the deliverable |
| Claude call fails or is slow during recording | Every AI feature has a deterministic fallback; cache results for the demo data |
| Python 3.9 on the laptop | Set up a 3.11+ venv first thing |
| Can't explain AI-written code | Each owner walks the team through their module before freeze |
