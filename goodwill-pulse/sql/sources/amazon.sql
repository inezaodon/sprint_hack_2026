-- data/sources/amazon.duckdb : Amazon Seller Central (books, sold through Cash Monkey, merchant fulfilled)
-- Native quirks the harmonizer must handle:
--   * purchase_date is ISO-8601 TEXT in UTC ('2026-09-30T23:41:07Z')
--   * financial_events.date_time is TEXT in Pacific time with a zone abbreviation ('Sep 30, 2026 4:41:07 PM PDT')
--   * fees are NEGATIVE numbers in financial_events; refunds appear as type='Refund' with negative product_sales
--   * store is only knowable from seller_sku: 'GWM-<2-digit store>-<n>'
CREATE TABLE IF NOT EXISTS orders (
    amazon_order_id      VARCHAR PRIMARY KEY,   -- '113-1234567-1234567'
    purchase_date        VARCHAR NOT NULL,      -- ISO-8601 UTC text
    last_updated_date    VARCHAR,
    order_status         VARCHAR,               -- Shipped | Unshipped | Canceled
    fulfillment_channel  VARCHAR,               -- 'MFN'
    sales_channel        VARCHAR,               -- 'Amazon.com'
    buyer_email          VARCHAR,               -- anonymized relay address 'abc123@marketplace.amazon.com'
    ship_state           VARCHAR,
    order_total_amount   DECIMAL(12,2),
    order_total_currency VARCHAR                -- 'USD'
);
CREATE TABLE IF NOT EXISTS order_items (
    amazon_order_id   VARCHAR NOT NULL,
    order_item_id     VARCHAR NOT NULL,
    seller_sku        VARCHAR NOT NULL,         -- 'GWM-03-123456'
    asin              VARCHAR,
    title             VARCHAR,
    quantity_ordered  INTEGER,
    item_price        DECIMAL(12,2),            -- price x quantity
    shipping_price    DECIMAL(12,2),            -- shipping credit charged to buyer for this item
    item_tax          DECIMAL(12,2),
    PRIMARY KEY (amazon_order_id, order_item_id)
);
-- "Date Range Transaction Report" rows (Payments > Reports Repository)
CREATE TABLE IF NOT EXISTS financial_events (
    event_id                VARCHAR PRIMARY KEY,
    date_time               VARCHAR NOT NULL,   -- 'Sep 30, 2026 4:41:07 PM PDT' (Pacific, PST/PDT)
    settlement_id           VARCHAR,
    type                    VARCHAR,            -- Order | Refund | Service Fee | Transfer
    order_id                VARCHAR,            -- null for Service Fee / Transfer
    sku                     VARCHAR,
    description             VARCHAR,
    quantity                INTEGER,
    marketplace             VARCHAR,            -- 'amazon.com'
    fulfillment             VARCHAR,            -- 'Seller'
    product_sales           DECIMAL(12,2),
    shipping_credits        DECIMAL(12,2),
    promotional_rebates     DECIMAL(12,2),
    selling_fees            DECIMAL(12,2),      -- negative
    fba_fees                DECIMAL(12,2),      -- 0 (MFN)
    other_transaction_fees  DECIMAL(12,2),      -- negative
    other                   DECIMAL(12,2),
    total                   DECIMAL(12,2)
);
CREATE TABLE IF NOT EXISTS settlements (
    settlement_id           VARCHAR PRIMARY KEY,
    settlement_start_date   DATE,
    settlement_end_date     DATE,
    deposit_date            DATE,
    total_amount            DECIMAL(12,2)       -- net deposited to 1st Source
);
-- Active/ended offers
CREATE TABLE IF NOT EXISTS listings (
    seller_sku      VARCHAR PRIMARY KEY,
    asin            VARCHAR,
    item_name       VARCHAR,
    open_date       VARCHAR,                    -- 'YYYY-MM-DD HH:MM:SS PDT' Pacific text
    price           DECIMAL(12,2),
    quantity        INTEGER,                    -- 0 once sold
    status          VARCHAR                     -- Active | Inactive | Sold
);
