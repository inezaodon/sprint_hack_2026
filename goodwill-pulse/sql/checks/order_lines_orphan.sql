-- severity: error
-- description: Every order line belongs to a loaded order, and every order has at least one line.
SELECT 'line_without_order' AS reason, l.order_key, l.line_no, l.sku, l.sale_price
FROM fct_order_lines l
WHERE NOT EXISTS (SELECT 1 FROM fct_orders o WHERE o.order_key = l.order_key)
UNION ALL
SELECT 'order_without_lines', o.order_key, NULL, NULL, o.subtotal
FROM fct_orders o
WHERE NOT EXISTS (SELECT 1 FROM fct_order_lines l WHERE l.order_key = o.order_key)
