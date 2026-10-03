-- severity: error
-- description: Every row listed in a source's _dirty_data table is excluded, flagged (NULL store) or fixed in harmonized; none leaks through as-is (test order loaded, duplicate double counted, non-canonical SKU/store/category/channel).
WITH canon_category(category) AS (VALUES ('Jewelry'), ('Collectibles'), ('Electronics'), ('Clothing'), ('Shoes'),
                                         ('Home Decor'), ('Toys & Games'), ('Art'), ('Watches'), ('Books')),
d AS (SELECT row_number() OVER () AS rid, * FROM dq_dirty),
ho AS (   -- harmonized orders a dirty row maps to
    SELECT d.rid, o.order_key
    FROM d JOIN fct_orders o
      ON o.source_db = d.source_db AND o.marketplace_order_id IN (d.order_ref, d.order_ref_alt)
),
hl AS (   -- harmonized listings a dirty listing row maps to
    SELECT d.rid, s.listing_key, s.sku, s.store_id, s.category, s.channel
    FROM d JOIN fct_listings s
      ON s.listing_key = d.source_db || ':' || d.listing_ref
),
bad_line AS (   -- a loaded line carrying a dirty (non-canonical, non-NULL) value
    SELECT ho.rid, count(*) AS n
    FROM ho JOIN fct_order_lines l USING (order_key)
    WHERE (l.store_id IS NOT NULL   -- NULL store = flagged as an exception, which is an accepted outcome
           AND (NOT regexp_full_match(coalesce(l.sku, ''), '(UP|GWM)-[0-9]{2}-[0-9]+')
                OR l.store_id NOT IN (SELECT store_id FROM dim_store)
                OR l.category IS NULL OR l.category NOT IN (SELECT category FROM canon_category)))
       OR l.channel NOT IN ('shopgoodwill', 'ebay', 'amazon', 'goodwillfinds', 'goodwillbooks')
    GROUP BY ho.rid
),
bad_listing AS (
    SELECT rid, count(*) AS n FROM hl
    WHERE (store_id IS NOT NULL   -- NULL store = flagged as an exception, which is an accepted outcome
           AND (NOT regexp_full_match(coalesce(sku, ''), '(UP|GWM)-[0-9]{2}-[0-9]+')
                OR store_id NOT IN (SELECT store_id FROM dim_store)
                OR category IS NULL OR category NOT IN (SELECT category FROM canon_category)))
    GROUP BY rid
),
bad_item AS (
    SELECT d.rid, count(*) AS n FROM d JOIN fct_items i ON i.item_id = d.item_ref OR upper(i.item_id) = upper(d.item_ref)
    WHERE i.store_id IS NOT NULL
      AND (NOT regexp_full_match(i.item_id, '(UP|GWM)-[0-9]{2}-[0-9]+')
           OR i.store_id NOT IN (SELECT store_id FROM dim_store)
           OR i.category IS NULL OR i.category NOT IN (SELECT category FROM canon_category))
    GROUP BY d.rid
),
dup AS (   -- duplicate rows: harmonized must not carry more lines / more money than the de-duplicated native order
    SELECT ho.rid
    FROM ho JOIN d USING (rid)
    JOIN fct_orders o USING (order_key)
    WHERE lower(d.kind) LIKE '%dup%'
      AND ((SELECT count(*) FROM fct_order_lines l WHERE l.order_key = ho.order_key)
            > (SELECT count(DISTINCT upper(n.sku)) FROM dq_native_lines n WHERE n.source_db = d.source_db AND n.order_ref = d.order_ref)
        OR o.subtotal > (SELECT sum(sale_price) FROM (SELECT DISTINCT upper(sku), sale_price FROM dq_native_lines n
                                                     WHERE n.source_db = d.source_db AND n.order_ref = d.order_ref)) + 0.01)
),
verdict AS (
    SELECT d.*,
        CASE
            WHEN d.order_ref IS NULL AND d.listing_ref IS NULL AND d.item_ref IS NULL THEN 'untraceable_native_key'
            WHEN lower(d.kind) LIKE '%test%' AND EXISTS (SELECT 1 FROM ho WHERE ho.rid = d.rid) THEN 'test_order_loaded'
            WHEN lower(d.kind) LIKE '%test%' AND EXISTS (SELECT 1 FROM hl WHERE hl.rid = d.rid) THEN 'test_listing_loaded'
            WHEN EXISTS (SELECT 1 FROM dup WHERE dup.rid = d.rid) THEN 'duplicate_double_counted'
            WHEN EXISTS (SELECT 1 FROM bad_line b WHERE b.rid = d.rid) THEN 'dirty_value_loaded_on_order_line'
            WHEN EXISTS (SELECT 1 FROM bad_listing b WHERE b.rid = d.rid) THEN 'dirty_value_loaded_on_listing'
            WHEN EXISTS (SELECT 1 FROM bad_item b WHERE b.rid = d.rid) THEN 'dirty_value_loaded_on_item'
        END AS reason
    FROM d
)
SELECT reason, source_db, table_name, native_key, kind, note, order_ref
FROM verdict WHERE reason IS NOT NULL
