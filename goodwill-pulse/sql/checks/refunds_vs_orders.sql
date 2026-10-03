-- severity: error
-- description: Every refund points at a loaded order, refunds never exceed the order total, and fct_orders.refund_amount equals the sum of its refunds.
WITH r AS (SELECT order_key, sum(amount) AS refunded, count(*) AS n FROM fct_refunds GROUP BY order_key)
SELECT 'refund_without_order' AS reason, f.refund_key AS row_key, f.order_key, f.amount AS refunded,
       NULL::DECIMAL(12,2) AS order_total, NULL::DECIMAL(12,2) AS order_refund_amount
FROM fct_refunds f
WHERE f.order_key IS NULL OR NOT EXISTS (SELECT 1 FROM fct_orders o WHERE o.order_key = f.order_key)
UNION ALL
SELECT 'refund_exceeds_order_total', o.order_key, o.order_key, r.refunded, o.total, o.refund_amount
FROM fct_orders o JOIN r USING (order_key)
WHERE r.refunded > o.total + 0.01
UNION ALL
SELECT 'refund_amount_mismatch', o.order_key, o.order_key, coalesce(r.refunded, 0), o.total, o.refund_amount
FROM fct_orders o LEFT JOIN r USING (order_key)
WHERE abs(coalesce(o.refund_amount, 0) - coalesce(r.refunded, 0)) > 0.01
UNION ALL
SELECT 'negative_refund', f.refund_key, f.order_key, f.amount, NULL, NULL
FROM fct_refunds f WHERE f.amount < 0
