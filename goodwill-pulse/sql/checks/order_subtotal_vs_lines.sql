-- severity: error
-- description: Order subtotal equals the sum of its line sale_price (and item_count equals the sum of line quantity).
WITH l AS (SELECT order_key, sum(sale_price) AS line_total, sum(quantity) AS line_qty
           FROM fct_order_lines GROUP BY order_key)
SELECT o.order_key, o.channel, o.business_date, o.subtotal, l.line_total, o.subtotal - l.line_total AS diff,
       o.item_count, l.line_qty
FROM fct_orders o JOIN l USING (order_key)
WHERE abs(coalesce(o.subtotal, 0) - coalesce(l.line_total, 0)) > 0.01
   OR o.item_count IS DISTINCT FROM l.line_qty
