-- severity: error
-- description: eBay is one seller account shared by Upright (UP- SKUs) and Cash Monkey (GWM- SKUs): each eBay order is counted once, its line_of_business matches its SKUs, and per-tool totals match native eBay.
WITH dirty AS (SELECT DISTINCT order_ref FROM dq_dirty WHERE source_db = 'ebay' AND order_ref IS NOT NULL),
lob AS (
    SELECT order_key,
           CASE WHEN bool_and(upper(sku) LIKE 'UP-%') THEN 'general_merch'
                WHEN bool_and(upper(sku) LIKE 'GWM-%') THEN 'books' ELSE 'mixed' END AS sku_lob
    FROM fct_order_lines GROUP BY order_key
),
nat AS (
    SELECT CASE WHEN upper(sku) LIKE 'GWM-%' THEN 'cashmonkey' ELSE 'upright' END AS tool,
           count(*) AS lines, sum(sale_price) AS amount
    FROM dq_native_lines WHERE source_db = 'ebay' AND order_ref NOT IN (SELECT order_ref FROM dirty)
    GROUP BY ALL
),
harm AS (
    SELECT CASE WHEN upper(l.sku) LIKE 'GWM-%' THEN 'cashmonkey' ELSE 'upright' END AS tool,
           count(*) AS lines, sum(l.sale_price) AS amount
    FROM fct_order_lines l JOIN fct_orders o USING (order_key)
    WHERE o.channel = 'ebay' AND o.marketplace_order_id NOT IN (SELECT order_ref FROM dirty)
    GROUP BY ALL
),
tools(tool) AS (VALUES ('upright'), ('cashmonkey'))
SELECT 'ebay_order_counted_twice' AS reason, marketplace_order_id AS row_key, NULL::VARCHAR AS detail,
       count(*) AS n, NULL::DECIMAL(12,2) AS native_amount, NULL::DECIMAL(12,2) AS harmonized_amount
FROM fct_orders WHERE channel = 'ebay' OR source_db = 'ebay'
GROUP BY marketplace_order_id HAVING count(*) > 1
UNION ALL
SELECT 'ebay_line_of_business_mismatch', o.order_key, o.line_of_business || ' vs SKUs ' || lob.sku_lob, 1, NULL, NULL
FROM fct_orders o JOIN lob USING (order_key)
WHERE o.channel = 'ebay' AND o.line_of_business IS DISTINCT FROM lob.sku_lob
UNION ALL
SELECT 'ebay_tool_total_mismatch', t.tool, 'lines ' || coalesce(nat.lines, 0) || ' native vs ' || coalesce(harm.lines, 0) || ' harmonized',
       coalesce(harm.lines, 0) - coalesce(nat.lines, 0), coalesce(nat.amount, 0), coalesce(harm.amount, 0)
FROM tools t LEFT JOIN nat USING (tool) LEFT JOIN harm USING (tool)
WHERE coalesce(nat.lines, 0) <> coalesce(harm.lines, 0) OR abs(coalesce(nat.amount, 0) - coalesce(harm.amount, 0)) > 0.01
