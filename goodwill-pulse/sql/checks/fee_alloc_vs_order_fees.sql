-- severity: error
-- description: Fees allocated to lines (fee_alloc) add back up to the order's marketplace_fees, to the cent.
WITH l AS (SELECT order_key, sum(coalesce(fee_alloc, 0)) AS alloc_total, count(*) FILTER (WHERE fee_alloc IS NULL) AS null_allocs
           FROM fct_order_lines GROUP BY order_key)
SELECT o.order_key, o.channel, o.business_date, o.marketplace_fees, l.alloc_total,
       coalesce(o.marketplace_fees, 0) - l.alloc_total AS diff, l.null_allocs
FROM fct_orders o JOIN l USING (order_key)
WHERE abs(coalesce(o.marketplace_fees, 0) - l.alloc_total) > 0.01
   OR (l.null_allocs > 0 AND coalesce(o.marketplace_fees, 0) <> 0)
