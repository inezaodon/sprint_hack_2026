-- 90_lineage.sql : one harmonize_runs row per build: source and output row counts so sql/checks and the PM can see
-- at a glance whether anything was lost between the native DBs and the canonical model.
-- run_id / started_at come from the TEMP table _run_ctx the runner (goodwill_pulse/harmonize.py) creates.
INSERT INTO harmonize_runs
SELECT r.run_id, r.started_at, now(),
       json_object(
         'amazon.orders', (SELECT count(*) FROM amazon.orders),
         'amazon.order_items', (SELECT count(*) FROM amazon.order_items),
         'amazon.financial_events', (SELECT count(*) FROM amazon.financial_events),
         'amazon.settlements', (SELECT count(*) FROM amazon.settlements),
         'amazon.listings', (SELECT count(*) FROM amazon.listings),
         'ebay.orders', (SELECT count(*) FROM ebay.orders),
         'ebay.line_items', (SELECT count(*) FROM ebay.line_items),
         'ebay.transactions', (SELECT count(*) FROM ebay.transactions),
         'ebay.payouts', (SELECT count(*) FROM ebay.payouts),
         'ebay.listings', (SELECT count(*) FROM ebay.listings),
         'shopgoodwill.auctions', (SELECT count(*) FROM shopgoodwill.auctions),
         'shopgoodwill.sales', (SELECT count(*) FROM shopgoodwill.sales),
         'shopgoodwill.seller_fees', (SELECT count(*) FROM shopgoodwill.seller_fees),
         'shopgoodwill.periodic_statements', (SELECT count(*) FROM shopgoodwill.periodic_statements),
         'goodwillfinds.orders', (SELECT count(*) FROM goodwillfinds.orders),
         'goodwillfinds.line_items', (SELECT count(*) FROM goodwillfinds.line_items),
         'goodwillfinds.refunds', (SELECT count(*) FROM goodwillfinds.refunds),
         'goodwillfinds.payouts', (SELECT count(*) FROM goodwillfinds.payouts),
         'goodwillfinds.products', (SELECT count(*) FROM goodwillfinds.products),
         'goodwillbooks.sales_orders', (SELECT count(*) FROM goodwillbooks.sales_orders),
         'goodwillbooks.sales_order_lines', (SELECT count(*) FROM goodwillbooks.sales_order_lines),
         'goodwillbooks.monthly_statements', (SELECT count(*) FROM goodwillbooks.monthly_statements),
         'goodwillbooks.inventory', (SELECT count(*) FROM goodwillbooks.inventory),
         'ops.stores', (SELECT count(*) FROM ops.stores),
         'ops.upright_items', (SELECT count(*) FROM ops.upright_items),
         'ops.operational_productivity', (SELECT count(*) FROM ops.operational_productivity),
         'ops.employees', (SELECT count(*) FROM ops.employees),
         'ops.timeclock', (SELECT count(*) FROM ops.timeclock),
         'ops.budget', (SELECT count(*) FROM ops.budget),
         'ops.monthly_inputs', (SELECT count(*) FROM ops.monthly_inputs),
         -- what staging dropped on purpose, so a reviewer can tell exclusions from losses
         'excluded.test_orders', (SELECT count(*) FROM stg_orders_all WHERE exclude_reason = 'test'),
         'excluded.canceled_orders', (SELECT count(*) FROM stg_orders_all WHERE exclude_reason = 'canceled'),
         'excluded.unparseable_paid_at', (SELECT count(*) FROM stg_orders_all WHERE exclude_reason IS NULL AND paid_at_utc IS NULL)
       ),
       json_object(
         'dim_store', (SELECT count(*) FROM dim_store),
         'dim_channel', (SELECT count(*) FROM dim_channel),
         'dim_date', (SELECT count(*) FROM dim_date),
         'fct_orders', (SELECT count(*) FROM fct_orders),
         'fct_order_lines', (SELECT count(*) FROM fct_order_lines),
         'fct_refunds', (SELECT count(*) FROM fct_refunds),
         'fct_fees', (SELECT count(*) FROM fct_fees),
         'fct_payouts', (SELECT count(*) FROM fct_payouts),
         'fct_listings', (SELECT count(*) FROM fct_listings),
         'fct_items', (SELECT count(*) FROM fct_items),
         'fct_labor', (SELECT count(*) FROM fct_labor),
         'fct_budget', (SELECT count(*) FROM fct_budget),
         'fct_monthly_inputs', (SELECT count(*) FROM fct_monthly_inputs)
       )
FROM _run_ctx r;
