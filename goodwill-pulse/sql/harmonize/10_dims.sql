-- 10_dims.sql : dimensions.

-- dim_store: the ops store master is the only list of real stores. Staging joins against it so a SKU that
-- encodes a non-existent store ('UP-99-…') is not credited to a phantom 'Store99'.
INSERT INTO dim_store
SELECT DISTINCT ON (upper(trim(store_id)))
       'Store' || lpad(regexp_extract(trim(store_id), '([0-9]+)$', 1), 2, '0') AS store_id,
       store_name, city, coalesce(ai_flagging, false), coalesce(ecom_eye, false)
FROM ops.stores
WHERE regexp_matches(trim(store_id), '[0-9]+$')
ORDER BY upper(trim(store_id));

-- dim_channel: canonical channels. pulse_row mirrors config/channels.yaml pulse_rows (tests/test_harmonize.py asserts
-- they agree). listing_tool: eBay is the one shared account that both Upright and Cash Monkey list into.
INSERT INTO dim_channel VALUES
    ('shopgoodwill',  'ShopGoodwill',  'ShopGoodwill',              'upright'),
    ('amazon',        'Amazon',        'Amazon',                    'cashmonkey'),
    ('ebay',          'eBay',          'eBay',                      'both'),
    ('goodwillfinds', 'GoodwillFinds', 'Other e-commerce channels', 'upright'),
    ('goodwillbooks', 'Goodwillbooks', 'Other e-commerce channels', 'cashmonkey');

-- dim_date: a fixed calendar wide enough for the synthetic window (2025-09-01 .. 2026-10-03), YoY comparisons
-- against Sept 2024 budgets, and some forward room. Fixed bounds keep the output deterministic.
INSERT INTO dim_date
SELECT CAST(d AS DATE)                       AS date,
       CAST(date_trunc('month', d) AS DATE)  AS month_start,
       year(d), month(d), weekofyear(d),
       dayname(d),
       isodow(d) IN (6, 7)
FROM generate_series(TIMESTAMP '2024-01-01', TIMESTAMP '2027-12-31', INTERVAL 1 DAY) AS t(d);
