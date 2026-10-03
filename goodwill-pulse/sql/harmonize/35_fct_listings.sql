-- 35_fct_listings.sql : listings on every channel, with the same store / line-of-business / category resolution
-- as order lines (see 30_fct_orders.sql) so sell-through by store or category lines up with sales.
INSERT INTO fct_listings
WITH l AS (
    SELECT * FROM stg_amazon_listings
    UNION ALL BY NAME SELECT * FROM stg_ebay_listings
    UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_listings
    UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_listings
    UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_listings
), items AS (
    SELECT norm_sku(item_id) AS sku, any_value(store_id) AS store_id, any_value(line_of_business) AS lob,
           any_value(category) AS category
    FROM ops.upright_items GROUP BY 1
), r AS (
    SELECT l.*, coalesce(ds.store_id, ds2.store_id) AS store_id,
           coalesce(lob_from_sku(l.sku), i.lob, channel_lob(l.channel)) AS line_of_business,
           i.category AS master_category
    FROM l
    LEFT JOIN dim_store ds  ON ds.store_id = l.store_native
    LEFT JOIN items i       ON i.sku = l.sku
    LEFT JOIN dim_store ds2 ON ds2.store_id = i.store_id
)
SELECT listing_key, channel, sku, store_id,
       CASE WHEN line_of_business = 'books' THEN 'Books'
            ELSE coalesce(canon_category(master_category), canon_category(category_native)) END AS category,
       line_of_business, listed_at_utc, ended_at_utc, status, price, relist_of
FROM r
WHERE sku IS NULL OR sku NOT IN (SELECT sku FROM test_skus)   -- test items are not inventory (see 30_fct_orders)
QUALIFY row_number() OVER (PARTITION BY listing_key ORDER BY listed_at_utc) = 1
ORDER BY listing_key;
