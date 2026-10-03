-- 30_fct_orders.sql : union the five staging layers and build fct_orders.

CREATE OR REPLACE TEMP TABLE stg_orders_all AS
SELECT * FROM stg_amazon_orders
UNION ALL BY NAME SELECT * FROM stg_ebay_orders
UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_orders
UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_orders
UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_orders;

-- Orders that count: not a test order, not canceled, and with a parseable paid time (business_date is NOT NULL
-- in the schema; an unparseable time would otherwise abort the build -- sql/checks compares source vs output counts).
CREATE OR REPLACE TEMP TABLE kept_orders AS
SELECT channel || ':' || marketplace_order_id AS order_key, *
FROM stg_orders_all
WHERE exclude_reason IS NULL AND paid_at_utc IS NOT NULL;

-- Lines with store / line of business / category resolved ONCE here, for every channel:
--   store_id : the store encoded natively (SKU / vendor / store_code), but only if it is a real store in dim_store;
--              otherwise the Upright item master's store. Native first because it is printed on the item at the
--              store that gets the credit; the master rescues missing/garbled codes. Neither -> NULL (a DQ exception,
--              never dropped).
--   line_of_business : SKU prefix (UP = general_merch, GWM = books), then item master, then the channel's only tool.
--   category : books are always 'Books'. Otherwise Goodwill's canonical category from the item master wins over
--              the marketplace's own taxonomy text, which is only a fallback through canon_category().
CREATE OR REPLACE TEMP TABLE lines_resolved AS
WITH lines_all AS (
    SELECT * FROM stg_amazon_lines
    UNION ALL BY NAME SELECT * FROM stg_ebay_lines
    UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_lines
    UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_lines
    UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_lines
), items AS (
    SELECT norm_sku(item_id) AS sku, any_value(store_id) AS store_id, any_value(line_of_business) AS lob,
           any_value(category) AS category
    FROM ops.upright_items GROUP BY 1
), r AS (
    SELECT l.channel || ':' || l.marketplace_order_id AS order_key, l.*,
           coalesce(ds.store_id, ds2.store_id) AS store_id,
           coalesce(lob_from_sku(l.sku), i.lob, channel_lob(l.channel)) AS line_of_business,
           i.category AS master_category
    FROM lines_all l
    LEFT JOIN dim_store ds  ON ds.store_id = l.store_native
    LEFT JOIN items i       ON i.sku = l.sku
    LEFT JOIN dim_store ds2 ON ds2.store_id = i.store_id
)
SELECT r.* EXCLUDE (master_category),
       CASE WHEN line_of_business = 'books' THEN 'Books'
            ELSE coalesce(canon_category(master_category), canon_category(category_native)) END AS category
FROM r
WHERE order_key IN (SELECT order_key FROM kept_orders);   -- lines of test / canceled / duplicate orders go too

CREATE OR REPLACE TEMP TABLE refunds_all AS
SELECT * FROM stg_amazon_refunds
UNION ALL BY NAME SELECT * FROM stg_ebay_refunds
UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_refunds
UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_refunds
UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_refunds;

INSERT INTO fct_orders
WITH lv AS (
    SELECT order_key, sum(quantity) AS item_count,
           CASE WHEN count(DISTINCT line_of_business) > 1 THEN 'mixed' ELSE min(line_of_business) END AS lob
    FROM lines_resolved GROUP BY 1
), rf AS (
    SELECT channel || ':' || marketplace_order_id AS order_key, sum(amount) AS refund_amount
    FROM refunds_all WHERE marketplace_order_id IS NOT NULL GROUP BY 1
)
SELECT o.order_key, o.channel, o.marketplace_order_id,
       o.channel                                      AS source_db,   -- one source DB per channel today
       coalesce(lv.lob, channel_lob(o.channel))       AS line_of_business,
       buyer_key(o.channel, o.buyer_native)           AS buyer_key,
       o.paid_at_utc,
       et_date(o.paid_at_utc)                         AS business_date,
       coalesce(lv.item_count, 0)                     AS item_count,
       o.subtotal, o.shipping_charged, o.handling, o.tax, o.marketplace_fees,
       CAST(coalesce(rf.refund_amount, 0) AS DECIMAL(12,2)) AS refund_amount,
       -- total is recomputed (not copied) so it means the same thing on every channel
       CAST(o.subtotal + o.shipping_charged + o.handling + o.tax AS DECIMAL(12,2)) AS total
FROM kept_orders o
LEFT JOIN lv ON lv.order_key = o.order_key
LEFT JOIN rf ON rf.order_key = o.order_key
ORDER BY o.paid_at_utc, o.order_key;
