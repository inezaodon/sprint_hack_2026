-- 31_fct_order_lines.sql : order lines with the order's fees allocated pro rata to sale_price.
-- Why pro rata: most channels only report fees per order, and per-item KPIs (margin by store / category) need a
-- per-line fee. Rounding: each line gets round(fees x share, 2); the cents lost to rounding go to the
-- highest-priced line (ties -> lowest line_no) so the lines ALWAYS sum exactly to fct_orders.marketplace_fees.
-- Zero-value orders (all lines $0) split fees equally.
INSERT INTO fct_order_lines
WITH base AS (
    SELECT l.*, o.marketplace_fees AS order_fees, o.business_date,
           sum(l.sale_price) OVER (PARTITION BY l.order_key) AS order_sales,
           count(*)          OVER (PARTITION BY l.order_key) AS n_lines,
           row_number() OVER (PARTITION BY l.order_key ORDER BY l.sale_price DESC, l.line_no) AS price_rank
    FROM lines_resolved l
    JOIN fct_orders o ON o.order_key = l.order_key
), raw AS (
    SELECT *, CAST(round(CASE WHEN order_sales <> 0 THEN order_fees * sale_price / order_sales
                              ELSE order_fees / n_lines END, 2) AS DECIMAL(12,2)) AS alloc_raw
    FROM base
)
SELECT order_key, line_no, channel, sku, store_id, category, line_of_business, title, quantity, sale_price,
       CAST(alloc_raw + CASE WHEN price_rank = 1
                             THEN order_fees - sum(alloc_raw) OVER (PARTITION BY order_key) ELSE 0 END
            AS DECIMAL(12,2)) AS fee_alloc,
       business_date
FROM raw
ORDER BY order_key, line_no;
