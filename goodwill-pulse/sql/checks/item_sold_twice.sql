-- severity: error
-- description: A donated item is one physical thing: a SKU is sold on at most one order line and has at most one sold listing.
SELECT 'sku_on_multiple_order_lines' AS reason, sku, count(*) AS n,
       string_agg(order_key || '#' || line_no, ', ' ORDER BY order_key) AS rows
FROM fct_order_lines WHERE sku IS NOT NULL
GROUP BY sku HAVING count(*) > 1
UNION ALL
SELECT 'sku_with_multiple_sold_listings', sku, count(*), string_agg(listing_key, ', ' ORDER BY listing_key)
FROM fct_listings WHERE status = 'sold' AND sku IS NOT NULL
GROUP BY sku HAVING count(*) > 1
