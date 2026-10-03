-- severity: error
-- description: Per source DB, native order count, line count and item subtotal equal the harmonized totals (orders listed in _dirty_data excluded on both sides).
WITH dirty AS (
    SELECT DISTINCT source_db, order_ref AS id FROM dq_dirty WHERE order_ref IS NOT NULL
    UNION SELECT DISTINCT source_db, order_ref_alt FROM dq_dirty WHERE order_ref_alt IS NOT NULL
),
nat AS (
    SELECT n.source_db, count(*) AS orders, sum(n.line_count) AS lines, sum(n.subtotal) AS subtotal
    FROM dq_native_orders n
    WHERE NOT EXISTS (SELECT 1 FROM dirty d WHERE d.source_db = n.source_db AND d.id IN (n.order_ref, n.order_ref_alt))
    GROUP BY n.source_db
),
lc AS (SELECT order_key, count(*) AS lines FROM fct_order_lines GROUP BY order_key),
harm AS (
    SELECT o.source_db, count(*) AS orders, sum(coalesce(lc.lines, 0)) AS lines, sum(o.subtotal) AS subtotal
    FROM fct_orders o LEFT JOIN lc USING (order_key)
    WHERE NOT EXISTS (SELECT 1 FROM dirty d WHERE d.source_db = o.source_db AND d.id = o.marketplace_order_id)
    GROUP BY o.source_db
),
src(source_db) AS (VALUES ('amazon'), ('ebay'), ('shopgoodwill'), ('goodwillfinds'), ('goodwillbooks'))
SELECT s.source_db,
       coalesce(nat.orders, 0) AS native_orders, coalesce(harm.orders, 0) AS harmonized_orders,
       coalesce(nat.lines, 0) AS native_lines, coalesce(harm.lines, 0) AS harmonized_lines,
       coalesce(nat.subtotal, 0) AS native_subtotal, coalesce(harm.subtotal, 0) AS harmonized_subtotal,
       coalesce(harm.subtotal, 0) - coalesce(nat.subtotal, 0) AS subtotal_diff
FROM src s LEFT JOIN nat USING (source_db) LEFT JOIN harm USING (source_db)
WHERE coalesce(nat.orders, 0) <> coalesce(harm.orders, 0)
   OR coalesce(nat.lines, 0) <> coalesce(harm.lines, 0)
   OR abs(coalesce(nat.subtotal, 0) - coalesce(harm.subtotal, 0)) > 0.01
