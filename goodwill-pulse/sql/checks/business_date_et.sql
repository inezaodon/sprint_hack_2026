-- severity: error
-- description: business_date is the America/New_York calendar date of the UTC timestamp (orders, refunds), and lines carry their order's date.
SELECT 'fct_orders' AS table_name, order_key AS row_key, paid_at_utc AS ts_utc, business_date,
       CAST(timezone('America/New_York', paid_at_utc) AS DATE) AS expected_date
FROM fct_orders
WHERE business_date IS DISTINCT FROM CAST(timezone('America/New_York', paid_at_utc) AS DATE)
UNION ALL
SELECT 'fct_refunds', refund_key, refunded_at_utc, business_date,
       CAST(timezone('America/New_York', refunded_at_utc) AS DATE)
FROM fct_refunds
WHERE business_date IS DISTINCT FROM CAST(timezone('America/New_York', refunded_at_utc) AS DATE)
UNION ALL
SELECT 'fct_order_lines', l.order_key || '#' || l.line_no, o.paid_at_utc, l.business_date, o.business_date
FROM fct_order_lines l JOIN fct_orders o USING (order_key)
WHERE l.business_date IS DISTINCT FROM o.business_date
