-- 23_stg_goodwillfinds.sql : GoodwillFinds.com (Shopify-style) -> staging.
-- Quirks: created_at is ISO text with an Eastern UTC OFFSET (-04:00 / -05:00) -> iso_ts() honours it;
-- store only from line_items.vendor ('Goodwill Michiana #03', sometimes mistyped) -> store_from_vendor();
-- category = product_type free text with random casing -> canon_category() fallback in 30.
-- Column layout: see header of 20_stg_amazon.sql.

-- Orders, deduplicated on the human order name '#GF10421': a duplicated API row gets a new numeric id but keeps
-- its name. Lines/refunds hanging off the dropped id disappear with it (30 keeps only lines of kept orders).
CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_orders AS
SELECT 'goodwillfinds' AS channel, CAST(o.id AS VARCHAR) AS marketplace_order_id,
       coalesce(CAST(o.customer_id AS VARCHAR), o.email_hash) AS buyer_native,
       iso_ts(o.created_at) AS paid_at_utc,
       coalesce(o.subtotal_price, 0)       AS subtotal,
       coalesce(o.total_shipping_price, 0) AS shipping_charged,
       CAST(0 AS DECIMAL(12,2))            AS handling,
       coalesce(o.total_tax, 0)            AS tax,
       coalesce(o.marketplace_fee, 0)      AS marketplace_fees,
       CASE WHEN is_test_buyer(o.email_hash) OR upper(coalesce(o.name, '')) LIKE '%TEST%' THEN 'test' END AS exclude_reason
FROM goodwillfinds.orders o
QUALIFY row_number() OVER (PARTITION BY coalesce(o.name, CAST(o.id AS VARCHAR)) ORDER BY o.id) = 1;

-- Lines: price is the UNIT price; extend by quantity. Duplicate rows collapse on (order, sku).
CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_lines AS
WITH dedup AS (
    SELECT * FROM goodwillfinds.line_items
    QUALIFY row_number() OVER (PARTITION BY order_id, coalesce(norm_sku(sku), CAST(id AS VARCHAR)) ORDER BY id) = 1
)
SELECT 'goodwillfinds' AS channel, CAST(order_id AS VARCHAR) AS marketplace_order_id,
       CAST(row_number() OVER (PARTITION BY order_id ORDER BY id) AS INTEGER) AS line_no,
       norm_sku(sku) AS sku,
       coalesce(store_from_vendor(vendor), store_from_sku(sku)) AS store_native,  -- vendor first, SKU if vendor unusable
       product_type AS category_native, title,
       coalesce(quantity, 1) AS quantity,
       CAST(coalesce(price, 0) * coalesce(quantity, 1) AS DECIMAL(12,2)) AS sale_price
FROM dedup;

CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_refunds AS
SELECT 'goodwillfinds:' || id AS refund_key, 'goodwillfinds' AS channel, CAST(order_id AS VARCHAR) AS marketplace_order_id,
       iso_ts(created_at) AS refunded_at_utc, CAST(abs(amount) AS DECIMAL(12,2)) AS amount
FROM goodwillfinds.refunds
QUALIFY row_number() OVER (PARTITION BY order_id, created_at, amount ORDER BY id) = 1;

-- The marketplace fee is a single per-order commission.
CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_fees AS
SELECT 'goodwillfinds:' || marketplace_order_id || ':commission' AS fee_key, channel, marketplace_order_id,
       'commission' AS fee_type, marketplace_fees AS amount, paid_at_utc AS fee_at_utc
FROM stg_goodwillfinds_orders WHERE marketplace_fees <> 0;

-- Weekly payouts carry only the pay date. Each one settles a Mon-Sun week (Eastern) and is paid the Wednesday
-- after it ends, so the period is the Mon-Sun week ending 3 days before the pay date.
CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_payouts AS
SELECT 'goodwillfinds:' || id AS payout_key, 'goodwillfinds' AS channel, date AS paid_on,
       CAST(date_trunc('week', date - INTERVAL 3 DAY) AS DATE) AS period_start,
       CAST(date - INTERVAL 3 DAY AS DATE) AS period_end,
       charges_gross AS gross, fees, refunds_gross AS refunds, amount AS net
FROM goodwillfinds.payouts;

CREATE OR REPLACE TEMP TABLE stg_goodwillfinds_listings AS
SELECT 'goodwillfinds:' || id AS listing_key, 'goodwillfinds' AS channel,
       norm_sku(sku) AS sku, coalesce(store_from_vendor(vendor), store_from_sku(sku)) AS store_native,
       product_type AS category_native,
       iso_ts(published_at) AS listed_at_utc, iso_ts(unpublished_at) AS ended_at_utc,
       CASE status WHEN 'active' THEN 'active' WHEN 'sold' THEN 'sold' WHEN 'archived' THEN 'unsold'
                   ELSE lower(status) END AS status,
       price, CAST(NULL AS VARCHAR) AS relist_of
FROM goodwillfinds.products;
