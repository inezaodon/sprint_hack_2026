-- 40_fct_ops.sql : item lifecycle, labor, budget and finance inputs from ops.duckdb (cleaned copies).

-- Items: item_id normalized like marketplace SKUs so joins to fct_order_lines.sku work. first_listed_at falls back
-- to the earliest marketplace listing when Upright has no posted_at; first_sold_at = earliest paid order line.
INSERT INTO fct_items
WITH lst AS (SELECT sku, min(listed_at_utc) AS first_listed FROM fct_listings WHERE sku IS NOT NULL GROUP BY 1),
     sold AS (SELECT l.sku, min(o.paid_at_utc) AS first_sold
              FROM fct_order_lines l JOIN fct_orders o USING (order_key) WHERE l.sku IS NOT NULL GROUP BY 1)
SELECT norm_sku(u.item_id) AS item_id,
       coalesce(ds.store_id, ds2.store_id) AS store_id,
       coalesce(u.line_of_business, lob_from_sku(u.item_id)) AS line_of_business,
       CASE WHEN coalesce(u.line_of_business, lob_from_sku(u.item_id)) = 'books' THEN 'Books'
            ELSE canon_category(u.category) END AS category,
       u.identified_at, u.flagged_by, u.manifested_at,
       coalesce(u.posted_at, lst.first_listed) AS first_listed_at_utc,
       sold.first_sold AS first_sold_at_utc,
       u.poster_id, u.list_minutes
FROM ops.upright_items u
LEFT JOIN dim_store ds  ON ds.store_id = u.store_id
LEFT JOIN dim_store ds2 ON ds2.store_id = store_from_sku(u.item_id)
LEFT JOIN lst  ON lst.sku  = norm_sku(u.item_id)
LEFT JOIN sold ON sold.sku = norm_sku(u.item_id)
WHERE norm_sku(u.item_id) IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY norm_sku(u.item_id) ORDER BY u.item_id) = 1
ORDER BY 1;

-- Labor: timeclock hours x hourly rate = cost; posted from Upright operational productivity. FULL OUTER JOIN so a
-- day with postings but no punch (or hours but no postings) is still visible to the productivity KPIs.
INSERT INTO fct_labor
SELECT coalesce(t.employee_id, p.employee_id) AS employee_id, e.role,
       coalesce(t.work_date, p.work_date) AS work_date,
       t.hours,
       CAST(round(t.hours * e.hourly_rate, 2) AS DECIMAL(12,2)) AS cost,
       p.posted
FROM ops.timeclock t
FULL OUTER JOIN ops.operational_productivity p
       ON p.employee_id = t.employee_id AND p.work_date = t.work_date
LEFT JOIN ops.employees e ON e.employee_id = coalesce(t.employee_id, p.employee_id)
ORDER BY 1, 3;

-- Budget: channel text normalized to the canonical lower-case channel ids; month forced to first-of-month.
INSERT INTO fct_budget
SELECT CAST(date_trunc('month', month) AS DATE), lower(trim(channel)), sum(revenue_budget)
FROM ops.budget GROUP BY 1, 2 ORDER BY 1, 2;

INSERT INTO fct_monthly_inputs
SELECT CAST(date_trunc('month', month) AS DATE), any_value(overhead_allocation), any_value(store_retail_revenue)
FROM ops.monthly_inputs GROUP BY 1 ORDER BY 1;
