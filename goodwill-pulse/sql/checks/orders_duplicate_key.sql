-- severity: error
-- description: Every order appears once: no duplicate order_key, and no marketplace order id loaded twice under different keys.
SELECT 'duplicate_order_key' AS reason, order_key, NULL::VARCHAR AS channel, NULL::VARCHAR AS marketplace_order_id,
       count(*) AS copies
FROM fct_orders GROUP BY order_key HAVING count(*) > 1
UNION ALL
SELECT 'same_marketplace_order_id_multiple_keys', string_agg(order_key, ', ' ORDER BY order_key), channel,
       marketplace_order_id, count(*)
FROM fct_orders GROUP BY channel, marketplace_order_id HAVING count(DISTINCT order_key) > 1
