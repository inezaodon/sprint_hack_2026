-- Helper views for the checks (NOT a check: files starting with '_' are skipped by goodwill_pulse.quality).
-- Runs on a connection where harmonized.duckdb is the default catalog and the source DBs are ATTACHed READ_ONLY as
-- amazon, ebay, shopgoodwill, goodwillfinds, goodwillbooks, ops. goodwill_pulse.quality first creates
-- TEMP VIEW dq_dirty_raw(source_db, table_name, native_key, kind, note) = UNION of every source's _dirty_data table.
--
-- native_key convention assumed (see docs/DATA_QUALITY.md): the native primary key as text; composite keys joined
-- with ':' (e.g. shopgoodwill.sales '123456:987654' = OrderID:ItemID, goodwillbooks lines 'GWB123456:2').

-- One row per native order, in the source's own shape, normalized to (source_db, order_ref, order_ref_alt, subtotal, line_count).
CREATE OR REPLACE TEMP VIEW dq_native_lines AS
SELECT 'amazon' AS source_db, i.amazon_order_id AS order_ref, i.seller_sku AS sku,
       CAST(i.item_price AS DECIMAL(12,2)) AS sale_price
FROM amazon.order_items i
UNION ALL
SELECT 'ebay', l.orderId, l.sku, TRY_CAST(l.lineItemCost AS DECIMAL(12,2))
FROM ebay.line_items l
UNION ALL
SELECT 'shopgoodwill', CAST(s.OrderID AS VARCHAR), a.SellerItemCode, s.HammerPrice
FROM shopgoodwill.sales s LEFT JOIN shopgoodwill.auctions a ON a.ItemID = s.ItemID
UNION ALL
SELECT 'goodwillfinds', CAST(l.order_id AS VARCHAR), l.sku, CAST(l.price * coalesce(l.quantity, 1) AS DECIMAL(12,2))
FROM goodwillfinds.line_items l
UNION ALL
SELECT 'goodwillbooks', l.order_no, l.sku, CAST(l.unit_price_cents * coalesce(l.qty, 1) / 100.0 AS DECIMAL(12,2))
FROM goodwillbooks.sales_order_lines l;

CREATE OR REPLACE TEMP VIEW dq_native_orders AS
WITH lc AS (SELECT source_db, order_ref, count(*) AS line_count, sum(sale_price) AS line_total
            FROM dq_native_lines GROUP BY ALL),
o AS (
    SELECT 'amazon' AS source_db, amazon_order_id AS order_ref, NULL::VARCHAR AS order_ref_alt,
           NULL::DECIMAL(12,2) AS header_subtotal
    FROM amazon.orders
    UNION ALL
    SELECT 'ebay', orderId, legacyOrderId, TRY_CAST(pricingSummary_priceSubtotal AS DECIMAL(12,2)) FROM ebay.orders
    UNION ALL
    SELECT DISTINCT 'shopgoodwill', CAST(OrderID AS VARCHAR), NULL, NULL FROM shopgoodwill.sales
    UNION ALL
    SELECT 'goodwillfinds', CAST(id AS VARCHAR), name, subtotal_price FROM goodwillfinds.orders
    UNION ALL
    SELECT 'goodwillbooks', order_no, NULL, CAST(items_cents / 100.0 AS DECIMAL(12,2)) FROM goodwillbooks.sales_orders
)
SELECT o.source_db, o.order_ref, o.order_ref_alt,
       coalesce(o.header_subtotal, lc.line_total, 0) AS subtotal,
       coalesce(lc.line_count, 0) AS line_count
FROM o LEFT JOIN lc USING (source_db, order_ref);

-- Every _dirty_data row traced to the native order / listing / item it is about.
CREATE OR REPLACE TEMP VIEW dq_dirty AS
WITH d AS (
    SELECT source_db, table_name, CAST(native_key AS VARCHAR) AS native_key, kind, note,
           regexp_extract(CAST(native_key AS VARCHAR), '^([^:|/,]+)', 1) AS key1
    FROM dq_dirty_raw
),
cand AS (   -- candidate native order ids for each dirty row
    SELECT d.*, CASE
        WHEN d.table_name NOT IN ('orders', 'order_items', 'line_items', 'sales', 'sales_orders', 'sales_order_lines',
                                  'financial_events', 'transactions', 'seller_fees', 'refunds') THEN NULL
        -- direct: the key (or its first part) is an order id (only for tables whose key starts with the order id)
        WHEN d.table_name IN ('orders', 'order_items', 'sales', 'sales_orders', 'sales_order_lines') AND EXISTS (SELECT 1 FROM dq_native_orders n WHERE n.source_db = d.source_db AND n.order_ref = d.key1) THEN d.key1
        WHEN d.source_db = 'amazon' THEN coalesce(
            (SELECT min(amazon_order_id) FROM amazon.order_items x WHERE x.order_item_id IN (d.native_key, d.key1)),
            (SELECT min(order_id) FROM amazon.financial_events x WHERE x.event_id IN (d.native_key, d.key1)))
        WHEN d.source_db = 'ebay' THEN coalesce(
            (SELECT min(orderId) FROM ebay.line_items x WHERE x.lineItemId IN (d.native_key, d.key1)),
            (SELECT min(orderId) FROM ebay.transactions x WHERE x.transactionId IN (d.native_key, d.key1)))
        WHEN d.source_db = 'shopgoodwill' THEN coalesce(
            CASE WHEN d.table_name = 'sales' THEN
                (SELECT CAST(min(OrderID) AS VARCHAR) FROM shopgoodwill.sales x WHERE x.ItemID = TRY_CAST(d.key1 AS BIGINT)) END,
            (SELECT CAST(min(OrderID) AS VARCHAR) FROM shopgoodwill.seller_fees x WHERE x.FeeID = TRY_CAST(d.key1 AS BIGINT)))
        WHEN d.source_db = 'goodwillfinds' THEN coalesce(
            (SELECT CAST(min(order_id) AS VARCHAR) FROM goodwillfinds.line_items x WHERE CAST(x.id AS VARCHAR) = d.key1),
            (SELECT CAST(min(order_id) AS VARCHAR) FROM goodwillfinds.refunds x WHERE CAST(x.id AS VARCHAR) = d.key1),
            (SELECT CAST(min(id) AS VARCHAR) FROM goodwillfinds.orders x WHERE x.name = d.key1))
        END AS order_ref
    FROM d
)
SELECT c.source_db, c.table_name, c.native_key, c.kind, c.note, c.order_ref,
       (SELECT min(order_ref_alt) FROM dq_native_orders n
        WHERE n.source_db = c.source_db AND n.order_ref = c.order_ref) AS order_ref_alt,
       CASE WHEN c.order_ref IS NULL AND c.table_name IN ('listings', 'auctions', 'products', 'inventory')
            THEN c.key1 END AS listing_ref,
       CASE WHEN c.source_db = 'ops' AND c.table_name = 'upright_items' THEN c.key1 END AS item_ref
FROM cand c;
