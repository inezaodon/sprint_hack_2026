-- severity: warning
-- description: Every business day between the first and last loaded order has at least one order on every channel (a gap usually means a missing feed, not a quiet day).
WITH b AS (SELECT min(business_date) AS lo, max(business_date) AS hi FROM fct_orders),
days AS (SELECT CAST(unnest(generate_series(lo, hi, INTERVAL 1 DAY)) AS DATE) AS business_date FROM b WHERE lo IS NOT NULL),
ch(channel) AS (VALUES ('shopgoodwill'), ('ebay'), ('amazon'), ('goodwillfinds'), ('goodwillbooks')),
have AS (SELECT DISTINCT business_date, channel FROM fct_orders)
SELECT date_trunc('month', d.business_date)::DATE AS month, d.business_date, c.channel
FROM days d CROSS JOIN ch c
WHERE NOT EXISTS (SELECT 1 FROM have h WHERE h.business_date = d.business_date AND h.channel = c.channel)
