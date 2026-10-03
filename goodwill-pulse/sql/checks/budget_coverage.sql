-- severity: warning
-- description: Every month with orders has a positive revenue budget for every channel (otherwise budget-vs-actual shows blank).
WITH m AS (SELECT DISTINCT date_trunc('month', business_date)::DATE AS month FROM fct_orders),
ch(channel) AS (VALUES ('shopgoodwill'), ('ebay'), ('amazon'), ('goodwillfinds'), ('goodwillbooks'))
SELECT m.month, c.channel, b.revenue_budget,
       CASE WHEN b.month IS NULL THEN 'missing_budget' ELSE 'non_positive_budget' END AS reason
FROM m CROSS JOIN ch c
LEFT JOIN fct_budget b ON b.month = m.month AND b.channel = c.channel
WHERE b.revenue_budget IS NULL OR b.revenue_budget <= 0
