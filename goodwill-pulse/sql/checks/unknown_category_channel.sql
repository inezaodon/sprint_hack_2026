-- severity: error
-- description: Channel is one of the 5 canonical channels (and in dim_channel); category is one of the 10 canonical categories (a NULL category on a line already flagged with a NULL store is left to store_id_missing).
WITH canon_channel(channel) AS (VALUES ('shopgoodwill'), ('ebay'), ('amazon'), ('goodwillfinds'), ('goodwillbooks')),
canon_category(category) AS (VALUES ('Jewelry'), ('Collectibles'), ('Electronics'), ('Clothing'), ('Shoes'),
                                    ('Home Decor'), ('Toys & Games'), ('Art'), ('Watches'), ('Books')),
vals AS (
    SELECT 'fct_orders' AS table_name, order_key AS row_key, channel, NULL::VARCHAR AS category, 'x' AS store_id FROM fct_orders
    UNION ALL SELECT 'fct_order_lines', order_key || '#' || line_no, channel, category, store_id FROM fct_order_lines
    UNION ALL SELECT 'fct_listings', listing_key, channel, category, store_id FROM fct_listings
    UNION ALL SELECT 'fct_refunds', refund_key, channel, NULL, 'x' FROM fct_refunds
    UNION ALL SELECT 'fct_payouts', payout_key, channel, NULL, 'x' FROM fct_payouts
    UNION ALL SELECT 'fct_budget', CAST(month AS VARCHAR) || ':' || channel, channel, NULL, 'x' FROM fct_budget
    UNION ALL SELECT 'fct_items', item_id, 'shopgoodwill', category, store_id FROM fct_items   -- items have no channel
)
SELECT table_name, row_key, channel, category,
       CASE WHEN channel IS NULL OR channel NOT IN (SELECT channel FROM canon_channel)
                 OR channel NOT IN (SELECT channel FROM dim_channel) THEN 'unknown_channel'
            ELSE 'unknown_category' END AS reason
FROM vals
WHERE channel IS NULL OR channel NOT IN (SELECT channel FROM canon_channel)
   OR channel NOT IN (SELECT channel FROM dim_channel)
   OR (table_name IN ('fct_order_lines', 'fct_listings', 'fct_items')
       AND ((category IS NULL AND store_id IS NOT NULL) OR category NOT IN (SELECT category FROM canon_category)))
