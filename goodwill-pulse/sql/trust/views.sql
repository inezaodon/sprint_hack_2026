-- Trust engine views over Odin's harmonized model (data/harmonized.duckdb).
-- Presents fct_* tables in the sprint plan's data contract (sales_line, books) so the trust engine runs unchanged.
-- TEMP views only: nothing is written to Odin's file. {channel_name} and {department} are filled in by
-- goodwill_pulse/trust/odin.py from its CHANNEL_NAMES and DEPARTMENTS maps.

-- One row per paid order line, plus refund rows (negative) spread across the order's lines.
CREATE OR REPLACE TEMP VIEW sales_line AS
WITH lines AS (
    SELECT l.order_key, l.line_no, l.sku, l.store_id, l.category, l.quantity, l.sale_price,
           o.channel, o.buyer_key, o.source_db, o.business_date, o.shipping_charged AS order_shipping,
           row_number() OVER (PARTITION BY l.order_key ORDER BY l.line_no) AS rn,
           SUM(l.sale_price) OVER (PARTITION BY l.order_key) AS order_sum
    FROM fct_order_lines l JOIN fct_orders o USING (order_key)
),
refund_shares AS (   -- each refund split across its order's lines pro rata to sale_price, rounded to cents
    SELECT r.refund_key, r.business_date AS refund_date, r.amount, ln.*,
           CAST(COALESCE(ROUND(r.amount * ln.sale_price / NULLIF(ln.order_sum, 0), 2), 0) AS DECIMAL(12,2)) AS share
    FROM fct_refunds r JOIN lines ln USING (order_key)
),
refund_lines AS (    -- rounding remainder goes on the first line, so lines add up to the refund exactly
    SELECT *, CASE WHEN rn = 1 THEN amount - (SUM(share) OVER (PARTITION BY refund_key) - share)
                   ELSE share END AS line_refund
    FROM refund_shares
)
SELECT order_key AS order_id, business_date AS paid_date, {channel_name} AS channel, {department} AS department,
       sku, store_id AS origin_store, quantity AS items, sale_price AS subtotal,
       CASE WHEN rn = 1 THEN order_shipping ELSE 0 END AS shipping_charged,   -- shipping is per order
       buyer_key AS buyer_id, 'paid' AS status, source_db AS source_file
FROM lines
UNION ALL
SELECT order_key, refund_date, {channel_name}, {department}, sku, store_id, 0, -line_refund, 0,
       buyer_key, 'refund', source_db
FROM refund_lines;

-- Weekly books per store. Scanned = book items identified at the store (Cash Monkey scan); sold = book order lines.
CREATE OR REPLACE TEMP VIEW books AS
WITH scanned AS (
    SELECT CAST(date_trunc('week', identified_at_utc AT TIME ZONE 'America/New_York') AS DATE) AS week,
           store_id AS store, count(*) AS books_scanned
    FROM fct_items WHERE line_of_business = 'books' AND identified_at_utc IS NOT NULL
    GROUP BY ALL
),
sold AS (
    SELECT CAST(date_trunc('week', business_date) AS DATE) AS week, store_id AS store,
           SUM(quantity) AS books_sold, SUM(sale_price) AS books_revenue
    FROM fct_order_lines WHERE line_of_business = 'books'
    GROUP BY ALL
)
SELECT COALESCE(s.week, d.week) AS week, COALESCE(s.store, d.store) AS store,
       COALESCE(s.books_scanned, 0) AS books_scanned, COALESCE(d.books_sold, 0) AS books_sold,
       COALESCE(d.books_revenue, 0) AS books_revenue, 'fct_items + fct_order_lines' AS source_file
FROM scanned s FULL OUTER JOIN sold d ON s.week = d.week AND s.store = d.store;
