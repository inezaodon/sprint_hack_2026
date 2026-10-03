-- severity: error
-- description: Order total = subtotal + shipping_charged + handling + tax (the schema's definition of total).
SELECT order_key, channel, business_date, subtotal, shipping_charged, handling, tax, total,
       total - (coalesce(subtotal, 0) + coalesce(shipping_charged, 0) + coalesce(handling, 0) + coalesce(tax, 0)) AS diff
FROM fct_orders
WHERE total IS NULL
   OR abs(total - (coalesce(subtotal, 0) + coalesce(shipping_charged, 0) + coalesce(handling, 0) + coalesce(tax, 0))) > 0.01
