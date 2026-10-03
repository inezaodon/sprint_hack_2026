-- 24_stg_goodwillbooks.sql : Goodwillbooks storefront (books via Cash Monkey) -> staging.
-- Quirks: money is INTEGER CENTS -> cents(); order_date is 'MM/DD/YYYY HH:MM' Eastern wall-clock with no zone
-- -> eastern_naive(strptime()); store only from store_code '03' (SKU fallback when it is missing).
-- Column layout: see header of 20_stg_amazon.sql.

CREATE OR REPLACE TEMP MACRO gwb_ts(s) AS eastern_naive(try_strptime(trim(s), '%m/%d/%Y %H:%M'));

CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_orders AS
SELECT 'goodwillbooks' AS channel, order_no AS marketplace_order_id, customer_ref AS buyer_native,
       gwb_ts(order_date) AS paid_at_utc,
       cents(coalesce(items_cents, 0))    AS subtotal,
       cents(coalesce(shipping_cents, 0)) AS shipping_charged,
       CAST(0 AS DECIMAL(12,2))           AS handling,
       cents(coalesce(tax_cents, 0))      AS tax,
       cents(coalesce(fee_cents, 0))      AS marketplace_fees,
       CASE WHEN is_test_buyer(customer_ref) THEN 'test' END AS exclude_reason
FROM goodwillbooks.sales_orders;

CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_lines AS
WITH dedup AS (
    SELECT * FROM goodwillbooks.sales_order_lines
    QUALIFY row_number() OVER (PARTITION BY order_no, coalesce(norm_sku(sku), CAST(line AS VARCHAR)) ORDER BY line) = 1
)
SELECT 'goodwillbooks' AS channel, order_no AS marketplace_order_id,
       CAST(row_number() OVER (PARTITION BY order_no ORDER BY line) AS INTEGER) AS line_no,
       norm_sku(sku) AS sku,
       coalesce(store_from_code(store_code), store_from_sku(sku)) AS store_native,  -- store_code first, SKU if missing
       'Books' AS category_native, title,
       coalesce(qty, 1) AS quantity,
       cents(coalesce(unit_price_cents, 0) * coalesce(qty, 1)) AS sale_price
FROM dedup;

-- Refunds live on the order header (one refund per order at most).
CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_refunds AS
SELECT 'goodwillbooks:' || order_no AS refund_key, 'goodwillbooks' AS channel, order_no AS marketplace_order_id,
       coalesce(gwb_ts(refund_date), gwb_ts(order_date)) AS refunded_at_utc,
       cents(abs(refund_cents)) AS amount
FROM goodwillbooks.sales_orders WHERE coalesce(refund_cents, 0) <> 0;

CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_fees AS
SELECT 'goodwillbooks:' || marketplace_order_id || ':commission' AS fee_key, channel, marketplace_order_id,
       'commission' AS fee_type, marketplace_fees AS amount, paid_at_utc AS fee_at_utc
FROM stg_goodwillbooks_orders WHERE marketplace_fees <> 0;

-- Monthly statement, paid the following month.
CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_payouts AS
SELECT 'goodwillbooks:' || statement_month AS payout_key, 'goodwillbooks' AS channel, paid_on,
       CAST(strptime(statement_month || '-01', '%Y-%m-%d') AS DATE) AS period_start,
       last_day(CAST(strptime(statement_month || '-01', '%Y-%m-%d') AS DATE)) AS period_end,
       cents(gross_cents) AS gross, cents(fees_cents) AS fees, cents(refunds_cents) AS refunds, cents(net_cents) AS net
FROM goodwillbooks.monthly_statements;

-- Inventory only has dates (MM/DD/YYYY); treat them as midnight Eastern.
CREATE OR REPLACE TEMP TABLE stg_goodwillbooks_listings AS
SELECT 'goodwillbooks:' || norm_sku(sku) AS listing_key, 'goodwillbooks' AS channel,
       norm_sku(sku) AS sku, coalesce(store_from_code(store_code), store_from_sku(sku)) AS store_native,
       'Books' AS category_native,
       eastern_naive(try_strptime(trim(listed_on), '%m/%d/%Y'))   AS listed_at_utc,
       eastern_naive(try_strptime(trim(delisted_on), '%m/%d/%Y')) AS ended_at_utc,
       CASE status WHEN 'LISTED' THEN 'active' WHEN 'SOLD' THEN 'sold' WHEN 'DELISTED' THEN 'unsold'
                   ELSE lower(status) END AS status,
       cents(price_cents) AS price, CAST(NULL AS VARCHAR) AS relist_of
FROM goodwillbooks.inventory
QUALIFY row_number() OVER (PARTITION BY norm_sku(sku) ORDER BY sku) = 1;
