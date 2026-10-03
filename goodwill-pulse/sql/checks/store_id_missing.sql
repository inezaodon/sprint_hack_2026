-- severity: warning
-- description: Every sold line and listing is credited to a known store (store_id NULL or not in dim_store is an exception to fix).
SELECT 'fct_order_lines' AS table_name, order_key || '#' || line_no AS row_key, sku, store_id, sale_price AS amount
FROM fct_order_lines
WHERE store_id IS NULL OR store_id NOT IN (SELECT store_id FROM dim_store)
UNION ALL
SELECT 'fct_listings', listing_key, sku, store_id, price
FROM fct_listings
WHERE store_id IS NULL OR store_id NOT IN (SELECT store_id FROM dim_store)
UNION ALL
SELECT 'fct_items', item_id, item_id, store_id, NULL
FROM fct_items
WHERE store_id IS NULL OR store_id NOT IN (SELECT store_id FROM dim_store)
