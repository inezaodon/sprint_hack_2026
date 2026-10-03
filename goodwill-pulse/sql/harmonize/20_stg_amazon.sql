-- 20_stg_amazon.sql : Amazon Seller Central (books via Cash Monkey, merchant fulfilled) -> staging.
-- Every 2x_stg_* file produces the same six TEMP tables with the same columns so 3x can UNION ALL BY NAME:
--   stg_<ch>_orders   (channel, marketplace_order_id, buyer_native, paid_at_utc, subtotal, shipping_charged, handling,
--                      tax, marketplace_fees, exclude_reason)
--   stg_<ch>_lines    (channel, marketplace_order_id, line_no, sku, store_native, category_native, title, quantity, sale_price)
--   stg_<ch>_refunds  (refund_key, channel, marketplace_order_id, refunded_at_utc, amount)
--   stg_<ch>_fees     (fee_key, channel, marketplace_order_id, fee_type, amount, fee_at_utc)
--   stg_<ch>_payouts  (payout_key, channel, paid_on, period_start, period_end, gross, fees, refunds, net)
--   stg_<ch>_listings (listing_key, channel, sku, store_native, category_native, listed_at_utc, ended_at_utc, status,
--                      price, relist_of)
-- Staging only normalizes types/time zones and removes duplicates; store/category/line-of-business resolution
-- happens once, centrally, in 30_fct_orders / 35_fct_listings.

-- Transaction report rows, deduplicated. A re-downloaded report can repeat a row under a new event_id, so the
-- natural key is the whole economic content of the row, not event_id.
CREATE OR REPLACE TEMP TABLE stg_amazon_events AS
SELECT e.*, amz_report_ts(e.date_time) AS event_at_utc
FROM amazon.financial_events e
QUALIFY row_number() OVER (
    PARTITION BY e.date_time, e.type, e.order_id, norm_sku(e.sku), e.settlement_id, e.total,
                 e.product_sales, e.selling_fees, e.other_transaction_fees
    ORDER BY e.event_id) = 1;

-- Order lines. A SKU is one physical item, so it can appear at most once per order: duplicate API rows collapse
-- on (order, normalized sku). item_price is already extended (price x quantity).
CREATE OR REPLACE TEMP TABLE stg_amazon_lines AS
WITH dedup AS (
    SELECT * FROM amazon.order_items
    QUALIFY row_number() OVER (PARTITION BY amazon_order_id, norm_sku(seller_sku) ORDER BY order_item_id) = 1
)
SELECT 'amazon'                                AS channel,
       amazon_order_id                         AS marketplace_order_id,
       CAST(row_number() OVER (PARTITION BY amazon_order_id ORDER BY order_item_id) AS INTEGER) AS line_no,
       norm_sku(seller_sku)                    AS sku,
       store_from_sku(seller_sku)              AS store_native,    -- Amazon knows the store only via the SKU
       CAST(NULL AS VARCHAR)                   AS category_native, -- Amazon browse nodes are not Goodwill categories
       title,
       coalesce(quantity_ordered, 1)           AS quantity,
       coalesce(item_price, 0)                 AS sale_price,
       coalesce(shipping_price, 0)             AS shipping_price,
       coalesce(item_tax, 0)                   AS item_tax
FROM dedup;

-- Orders. Amazon order-level money is the sum of its lines (order_total_amount mixes in tax and shipping).
-- Fees come from the transaction report: fees are NEGATIVE there, so flip the sign (positive = cost).
-- Only 'Order' events are the order's own fees; fee reimbursements on 'Refund' events stay in fct_fees.
CREATE OR REPLACE TEMP TABLE stg_amazon_orders AS
WITH l AS (
    SELECT marketplace_order_id, sum(sale_price) AS subtotal, sum(shipping_price) AS shipping, sum(item_tax) AS tax
    FROM stg_amazon_lines GROUP BY 1
), f AS (
    SELECT order_id,
           -sum(coalesce(selling_fees,0) + coalesce(fba_fees,0) + coalesce(other_transaction_fees,0)) AS fees
    FROM stg_amazon_events WHERE type = 'Order' AND order_id IS NOT NULL GROUP BY 1
)
SELECT 'amazon'                         AS channel,
       o.amazon_order_id                AS marketplace_order_id,
       o.buyer_email                    AS buyer_native,
       iso_ts(o.purchase_date)          AS paid_at_utc,      -- ISO-8601 UTC text
       CAST(coalesce(l.subtotal, 0) AS DECIMAL(12,2)) AS subtotal,
       CAST(coalesce(l.shipping, 0) AS DECIMAL(12,2)) AS shipping_charged,
       CAST(0 AS DECIMAL(12,2))         AS handling,         -- handling is an Upright concept; Amazon has none
       CAST(coalesce(l.tax, 0) AS DECIMAL(12,2))      AS tax,
       CAST(coalesce(f.fees, 0) AS DECIMAL(12,2))     AS marketplace_fees,
       CASE WHEN is_test_buyer(o.buyer_email) THEN 'test'
            WHEN o.order_status = 'Canceled'   THEN 'canceled'  -- never paid, not revenue
       END                              AS exclude_reason
FROM amazon.orders o
LEFT JOIN l ON l.marketplace_order_id = o.amazon_order_id
LEFT JOIN f ON f.order_id = o.amazon_order_id;

-- Refunds: type='Refund' rows carry NEGATIVE product_sales / shipping_credits; amount given back = -(their sum).
CREATE OR REPLACE TEMP TABLE stg_amazon_refunds AS
SELECT 'amazon:' || event_id            AS refund_key,
       'amazon'                         AS channel,
       order_id                         AS marketplace_order_id,
       event_at_utc                     AS refunded_at_utc,
       CAST(-(coalesce(product_sales,0) + coalesce(shipping_credits,0)) AS DECIMAL(12,2)) AS amount
FROM stg_amazon_events
WHERE type = 'Refund';

-- Fees: one row per fee component per event. selling_fees = referral fee; other_transaction_fees + fba_fees
-- (variable closing fee for media; FBA is 0 for MFN) = other. On Refund events the signs flip, which correctly
-- records the reimbursed referral fee as a negative cost. 'Service Fee' rows are account-level (subscription),
-- with no order: the whole row total is the fee.
CREATE OR REPLACE TEMP TABLE stg_amazon_fees AS
SELECT * FROM (
    SELECT 'amazon:' || event_id || ':referral' AS fee_key, 'amazon' AS channel, order_id AS marketplace_order_id,
           'referral' AS fee_type, CAST(-coalesce(selling_fees,0) AS DECIMAL(12,2)) AS amount, event_at_utc AS fee_at_utc
    FROM stg_amazon_events WHERE type IN ('Order', 'Refund')
    UNION ALL
    SELECT 'amazon:' || event_id || ':other', 'amazon', order_id, 'other',
           CAST(-(coalesce(other_transaction_fees,0) + coalesce(fba_fees,0)) AS DECIMAL(12,2)), event_at_utc
    FROM stg_amazon_events WHERE type IN ('Order', 'Refund')
    UNION ALL
    SELECT 'amazon:' || event_id || ':service', 'amazon', NULL, 'other',
           CAST(-coalesce(total,0) AS DECIMAL(12,2)), event_at_utc
    FROM stg_amazon_events WHERE type = 'Service Fee'
) WHERE amount <> 0;

-- Payouts: one settlement = one deposit to 1st Source 0101. net is what the bank sees (total_amount);
-- gross/fees/refunds are rebuilt from the settlement's transaction rows for reconciliation.
CREATE OR REPLACE TEMP TABLE stg_amazon_payouts AS
WITH ev AS (
    SELECT settlement_id,
           sum(CASE WHEN type = 'Order'  THEN coalesce(product_sales,0) + coalesce(shipping_credits,0) ELSE 0 END) AS gross,
           -sum(coalesce(selling_fees,0) + coalesce(fba_fees,0) + coalesce(other_transaction_fees,0)
                + CASE WHEN type = 'Service Fee' THEN coalesce(total,0) ELSE 0 END)                             AS fees,
           -sum(CASE WHEN type = 'Refund' THEN coalesce(product_sales,0) + coalesce(shipping_credits,0) ELSE 0 END) AS refunds
    FROM stg_amazon_events GROUP BY 1
)
SELECT 'amazon:' || s.settlement_id AS payout_key, 'amazon' AS channel,
       s.deposit_date AS paid_on, s.settlement_start_date AS period_start, s.settlement_end_date AS period_end,
       CAST(ev.gross AS DECIMAL(12,2)) AS gross, CAST(ev.fees AS DECIMAL(12,2)) AS fees,
       CAST(ev.refunds AS DECIMAL(12,2)) AS refunds, s.total_amount AS net
FROM amazon.settlements s
LEFT JOIN ev ON ev.settlement_id = s.settlement_id;

-- Listings: seller_sku is the offer key (one physical book = one SKU = one offer). The report has no end date,
-- so a sold offer ends at its order's purchase time; an Inactive (unsold) offer's end is unknown -> NULL.
CREATE OR REPLACE TEMP TABLE stg_amazon_listings AS
WITH sold AS (
    SELECT l.sku, min(o.paid_at_utc) AS sold_at
    FROM stg_amazon_lines l JOIN stg_amazon_orders o USING (marketplace_order_id)
    WHERE o.exclude_reason IS NULL GROUP BY 1
)
SELECT 'amazon:' || norm_sku(a.seller_sku) AS listing_key, 'amazon' AS channel,
       norm_sku(a.seller_sku) AS sku, store_from_sku(a.seller_sku) AS store_native,
       CAST(NULL AS VARCHAR) AS category_native,
       amz_listing_ts(a.open_date) AS listed_at_utc,
       CASE WHEN a.status = 'Sold' THEN sold.sold_at END AS ended_at_utc,
       CASE a.status WHEN 'Active' THEN 'active' WHEN 'Sold' THEN 'sold' WHEN 'Inactive' THEN 'unsold'
                     ELSE lower(a.status) END AS status,
       a.price,
       CAST(NULL AS VARCHAR) AS relist_of
FROM amazon.listings a
LEFT JOIN sold ON sold.sku = norm_sku(a.seller_sku)
QUALIFY row_number() OVER (PARTITION BY norm_sku(a.seller_sku) ORDER BY a.seller_sku) = 1;
