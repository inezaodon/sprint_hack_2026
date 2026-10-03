-- data/sources/goodwillfinds.duckdb : GoodwillFinds.com (Shopify-style marketplace; listed via Upright)
-- Native quirks:
--   * created_at is TEXT with a UTC OFFSET in Eastern time ('2026-09-30T19:41:07-04:00')
--   * store only from line_items.vendor: 'Goodwill Michiana #03'
--   * category is product_type free text with inconsistent casing ('jewelry', 'Jewelry', 'JEWELRY')
CREATE TABLE IF NOT EXISTS orders (
    id                    BIGINT PRIMARY KEY,
    name                  VARCHAR,               -- '#GF10421'
    created_at            VARCHAR NOT NULL,      -- ISO text with -04:00/-05:00
    financial_status      VARCHAR,               -- paid | refunded | partially_refunded
    customer_id           BIGINT,
    email_hash            VARCHAR,
    shipping_province     VARCHAR,
    subtotal_price        DECIMAL(12,2),
    total_shipping_price  DECIMAL(12,2),
    total_tax             DECIMAL(12,2),
    total_price           DECIMAL(12,2),
    marketplace_fee       DECIMAL(12,2),
    currency              VARCHAR
);
CREATE TABLE IF NOT EXISTS line_items (
    id            BIGINT PRIMARY KEY,
    order_id      BIGINT NOT NULL,
    sku           VARCHAR,                       -- 'UP-03-000123'
    title         VARCHAR,
    vendor        VARCHAR,                       -- 'Goodwill Michiana #03'
    product_type  VARCHAR,                       -- messy category text
    price         DECIMAL(12,2),
    quantity      INTEGER
);
CREATE TABLE IF NOT EXISTS refunds (
    id          BIGINT PRIMARY KEY,
    order_id    BIGINT,
    created_at  VARCHAR,                         -- ISO text with offset
    amount      DECIMAL(12,2),
    note        VARCHAR
);
CREATE TABLE IF NOT EXISTS payouts (
    id              BIGINT PRIMARY KEY,
    date            DATE,
    amount          DECIMAL(12,2),
    status          VARCHAR,                     -- paid
    charges_gross   DECIMAL(12,2),
    fees            DECIMAL(12,2),
    refunds_gross   DECIMAL(12,2)
);
CREATE TABLE IF NOT EXISTS products (
    id            BIGINT PRIMARY KEY,
    sku           VARCHAR,
    title         VARCHAR,
    vendor        VARCHAR,
    product_type  VARCHAR,
    published_at  VARCHAR,                       -- ISO text with offset
    unpublished_at VARCHAR,
    status        VARCHAR,                       -- active | sold | archived
    price         DECIMAL(12,2)
);
