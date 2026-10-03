# KPI definitions

Owner: Engineer 6. Code: `goodwill_pulse/kpi.py` (one documented function per KPI, registry `KPIS`).
Tests: `tests/test_kpi.py` (hand-computed values on a tiny harmonized DB). Input: `data/harmonized.duckdb` only.

★ = 2027 anchor (slide 36) · **S** = in the 15-KPI COO scorecard (slide 35) · Availability: `built` (computed from
harmonized data) · `input` (needs a monthly manual input: budget, overhead, store retail) · `n/a` (no source; value is
always `None`, shown as "not yet available").

## Conventions

| Topic | Decision |
|---|---|
| Month | Calendar month in America/New_York. Orders, refunds, fees, labor use `business_date`; listing/item timestamps (`*_utc`) are converted to New York before bucketing. |
| Units | `pct` values are fractions (0.1234 = 12.34 %), rounded to 4 dp; `usd`, `days`, `usd_per_hour`, `count` rounded to 2 dp. |
| Missing data | `None`, never 0. A month counts as covered when the unfiltered fact table has rows in it. Inside a covered month an empty filter result is a real 0 for sums/counts; a ratio with a zero or missing denominator is `None`. A filter that a fact has no column for (for example a channel filter on the unlisted backlog) returns `None`. |
| Revenue | Item subtotal: `fct_orders.subtotal` with no filter or a channel filter (the same basis as the Daily Pulse). Under a store or category filter it is `fct_order_lines.sale_price`, and the originating store gets the credit. Shipping, handling and tax are excluded. |
| Store filter | Revenue, fees and units come from `fct_order_lines.store_id`. Refunds and shipping labels are order-level, so they are split across lines pro rata to `sale_price`. Inventory KPIs use the store on the item's first listing (`fct_listings.store_id`); identification, manifest and backlog KPIs use `fct_items.store_id`. |
| Channel filter | Uses the channel column of each fact. The listing cohort uses the channel of the item's first listing. Budget is per channel. Items that are not yet listed have no channel. |
| Category filter | An optional extra filter that is not in the contract. It is used for the top-10 category views and by `breakdown(..., by="category")`. |
| Labor allocation | `fct_labor` has no store or channel. Under a filter, hours, cost and FTE are multiplied by the filter's share of the month's **listings created**. Listing is the work e-com staff spend their hours on, so a store that sends many items "uses" more labor. Revenue per labor hour therefore varies by store: it shows revenue yield per unit of listing effort. Listings per employee then equals the company value, because it is listings ÷ (FTE × listing share). |
| Overhead allocation | Multiplied by the filter's share of the month's revenue. |
| FTE | Hours ÷ 173.33 (40 h × 52 weeks ÷ 12). |
| Status | Each card gets `good`, `watch`, `bad` or `None`. It compares the value with the same month last year in the KPI's better direction. For `pct` KPIs: no worse than last year → good; within 1 point worse → watch; more than that → bad. For other KPIs the bands are relative: within 5 % worse → watch. With no prior-year value the status is `None`. Exceptions: **sell-through** is judged against the 50–55 % store target (≥ 50 % good, 45–50 % watch, < 45 % bad). **Revenue growth** is judged on its sign (≥ 0 good, down to −5 % watch). **Budget attainment**: ≥ 100 % good, ≥ 95 % watch. |
| Provisional | A card is `provisional` when the data's `as_of` date (last order `business_date`) falls inside the month. A sell-through card is also provisional until 30 days after the end of its month. |

## Catalog

| id | Pillar | Formula | Tables | Avail. | ★ | S |
|---|---|---|---|---|---|---|
| `total_revenue` | growth | Σ item subtotal in the month | fct_orders; fct_order_lines (store/category) | built | | S |
| `revenue_growth_yoy` | growth | (rev(m) − rev(m−12)) ÷ rev(m−12) | fct_orders | built | | S |
| `budget_attainment` | growth | revenue ÷ Σ revenue_budget (channel-level; `None` under store/category) | fct_orders, fct_budget | input | | |
| `ecom_share_of_retail` | growth | e-com rev ÷ (e-com rev + store retail revenue); company level only | fct_orders, fct_monthly_inputs | input | | |
| `net_margin` | profitability | (revenue − marketplace fees − shipping label cost − refunds − labor cost − allocated overhead) ÷ revenue | fct_orders/lines, fct_refunds, fct_fees, fct_labor, fct_monthly_inputs | input | ★ | S |
| `gross_margin` | profitability | (revenue − marketplace fees − shipping label cost − refunds) ÷ revenue. Donated goods have no purchase cost | fct_orders/lines, fct_refunds, fct_fees | built | | |
| `profit_per_labor_hour` | profitability | (gross profit − labor cost) ÷ labor hours | + fct_labor | built | | |
| `top_categories_by_margin` | profitability | Rank categories by gross profit $. Value = the #1 category's gross profit; `breakdown` holds the top 10 | fct_order_lines, fct_refunds, fct_fees | built | | S |
| `revenue_per_labor_hour` | productivity | revenue ÷ e-com labor hours (all roles), same month | fct_orders, fct_labor | built | ★ | S |
| `listings_created` | productivity | count of items whose **first** listing (any channel) started in the month; relists are excluded | fct_listings | built | | S |
| `listings_per_day` | productivity | listings created ÷ days with labor hours | fct_listings, fct_labor | built | | |
| `listings_per_employee` | productivity | listings created ÷ lister FTE (lister hours ÷ 173.33) | fct_listings, fct_labor | built | | S |
| `sales_per_employee` | productivity | revenue ÷ e-com FTE (all roles) | fct_orders, fct_labor | built | | S |
| `avg_time_to_list` | productivity | avg(first listed − manifested_at) in days, for items first listed in the month | fct_listings, fct_items | built | | |
| `items_identified` | productivity | items with `identified_at` in the month (no channel) | fct_items | built | | |
| `items_sent` | productivity | items with `manifested_at` in the month (no channel) | fct_items | built | | |
| `sell_through` | inventory | Items whose first listing is in month M and which sold (first order line, any channel) within 30 days ÷ items first listed in M | fct_listings, fct_order_lines, fct_orders | built | ★ | S |
| `days_donation_to_listing` | inventory | avg(first listed − identified_at) in days, for items first listed in the month | fct_listings, fct_items | built | | S |
| `unlisted_backlog` | inventory | items manifested before the month ends and not first-listed by then (`fct_items.first_listed_at_utc`). `None` under a channel filter | fct_items | built | | S |
| `unsold_pct` | inventory | listings that ended `unsold` in the month ÷ listings that ended `sold` or `unsold` (relists count as listings) | fct_listings | built | | S |
| `days_to_sell` | inventory | avg(first sale `paid_at` − first listed) for items whose first sale is in the month | fct_listings, fct_order_lines, fct_orders | built | | |
| `relisted_pct` | inventory | distinct items with a relist (`relist_of` not null) started in the month ÷ distinct items with any listing started in the month | fct_listings | built | | |
| `avg_selling_price` | inventory | Σ line `sale_price` ÷ Σ quantity | fct_order_lines | built | | S |
| `median_sale_price` | inventory | median unit price over order lines | fct_order_lines | built | | |
| `top_categories_by_revenue` | inventory | Rank categories by Σ line `sale_price`. Value = the #1 category's revenue; `breakdown` holds the top 10 | fct_order_lines | built | | S |
| `repeat_buyer_rate` | engagement | buyers in the month with any order before the 1st of the month ÷ buyers in the month | fct_orders (buyer_key) | built | | S |
| `buyers` | engagement | distinct `buyer_key` with an order in the month | fct_orders | built | | |
| `new_buyers` | engagement | buyers whose first-ever order is in the month | fct_orders | built | | |
| `refund_rate` | engagement | refunds $ (by refund date) ÷ revenue | fct_refunds, fct_orders | built | | |
| `customer_satisfaction` | engagement | marketplace seller rating | none | n/a | | |
| `net_promoter_score` | engagement | survey | none | n/a | | |
| `marketplace_conversion` | engagement | sold ÷ viewed or bid | none | n/a | | |

Revenue by marketplace, margin by category and sell-through by category are not separate KPIs. Get them with
`kpi.breakdown(con, kpi_id, month, by="channel"|"category"|"store")`.

The scorecard order (slide 35, 5 rows × 3; the first two cells of each row are exact, the third is inferred because the slide is cropped) is:

- **Financial:** total revenue, revenue growth YoY, net margin
- **Productivity:** listings created, revenue per labor hour, listings per employee
- **Inventory:** days from donation to listing, unlisted inventory backlog, unsold inventory %
- **Sales:** average selling price, sell-through rate, sales per employee
- **Category + Customer:** top 10 categories by revenue, top 10 categories by margin, repeat buyer rate

## Decisions made where the plan was ambiguous

1. **Sell-through** uses the listing-month cohort with a 30-day window. A cohort is the set of items first listed in M; it does not include relists or later listings. An item counts as sold if it sold on any channel, so an item first listed on ShopGoodwill and sold on eBay counts as sold. The newest cohort is provisional until its 30-day window closes, so September's value will rise through October.
2. **Net margin costs.** Marketplace fees come from `fct_orders.marketplace_fees` (`fee_alloc` under a store filter) and are attributed to the order's business date. Refunds are counted by refund date (cash basis), so a September order refunded in October hits October. Shipping label cost comes from `fct_fees` rows with `fee_type = 'shipping_label'` (contract v1.2). Until the harmonizer emits them, the cost is excluded and margin cards carry a note. Account-level fees (`fct_fees.order_key IS NULL`, for example store subscriptions) are not deducted.
3. **Repeat buyer.** A buyer is repeat in a month if they ordered before the 1st of that month, from any store. `buyer_key` is scoped to a channel, so a person who buys on both eBay and ShopGoodwill counts as two buyers. History starts 2025-09-01: in the first month with orders, repeat buyer rate and new buyers are `None` (unknowable, so status is `None` too); later early months still understate repeats. Revenue growth YoY is `None` when the month 12 months earlier has no orders.
4. **Days from donation to listing** uses `identified_at` (flagged at the store) as the donation proxy. **Average time to list** uses `manifested_at` (received by e-com).
5. **Top-10 KPIs** need a scalar `value`, so the value is the #1 category's figure. The ranked list is in `card["breakdown"]`.

## Open questions for Goodwill

- Sell-through: is 30 days the right window? Should relists restart the clock? What is the e-com target? (Stores use 50–55 %.)
- Net margin: which costs go in? Shipping labels and supplies, payment fees, account fees? Who provides the overhead allocation each month, and how is it split across stores?
- Labor: is all timeclock time e-com labor? Should managers be in revenue per labor hour? Is allocating by listing share acceptable for store views?
- Should refunds count by refund date (current choice) or against the original sale month?
- Should repeat buyers be matched across marketplaces? (That needs PII matching, which we deliberately do not do.)
- Does Upright record the donation date, or only identification and manifest dates?

## API (contract section 4, plus additive extras)

`compute(con, kpi_id, month, store=None, channel=None, category=None)`

`kpi_card(...)` returns the contract keys plus `better`, `anchor`, `scorecard`, `scorecard_row`, `provisional`, `notes`, `as_of`, and `breakdown` on top-10 cards.

`scorecard(con, month, store=None, channel=None, category=None)` returns `{month, as_of, filters, anchors, scorecard, pillars}`.

`series(con, kpi_id, months=13, store=None, channel=None, end=None, category=None)`. When `end` is not given, the series ends at the latest month with orders.

`breakdown(con, kpi_id, month, by, ...)`, `default_month(con)`, `connect_harmonized(path=None)`.
