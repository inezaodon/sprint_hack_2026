-- data/sources/goodwillbooks.duckdb : Goodwillbooks storefront (books; listed via Cash Monkey)
-- Native quirks:
--   * money is INTEGER CENTS
--   * order_date is TEXT 'MM/DD/YYYY HH:MM' in Eastern local time, no zone
--   * store only from store_code: 2-digit text '03'
CREATE TABLE IF NOT EXISTS sales_orders (
    order_no        VARCHAR PRIMARY KEY,         -- 'GWB123456'
    order_date      VARCHAR NOT NULL,            -- 'MM/DD/YYYY HH:MM' Eastern
    customer_ref    VARCHAR,
    ship_state      VARCHAR,
    items_cents     BIGINT,
    shipping_cents  BIGINT,
    tax_cents       BIGINT,
    fee_cents       BIGINT,                      -- platform fee
    total_cents     BIGINT,
    status          VARCHAR,                     -- COMPLETE | REFUNDED | PARTIAL_REFUND
    refund_cents    BIGINT,
    refund_date     VARCHAR                      -- 'MM/DD/YYYY HH:MM' Eastern
);
CREATE TABLE IF NOT EXISTS sales_order_lines (
    order_no          VARCHAR NOT NULL,
    line              INTEGER NOT NULL,
    isbn              VARCHAR,
    sku               VARCHAR,                   -- 'GWM-03-123456'
    title             VARCHAR,
    store_code        VARCHAR,                   -- '03'
    qty               INTEGER,
    unit_price_cents  BIGINT,
    PRIMARY KEY (order_no, line)
);
CREATE TABLE IF NOT EXISTS monthly_statements (
    statement_month VARCHAR PRIMARY KEY,         -- 'YYYY-MM'
    gross_cents     BIGINT,
    fees_cents      BIGINT,
    refunds_cents   BIGINT,
    net_cents       BIGINT,
    paid_on         DATE                         -- paid the following month (emailed PDF statement)
);
CREATE TABLE IF NOT EXISTS inventory (
    sku          VARCHAR PRIMARY KEY,
    isbn         VARCHAR,
    title        VARCHAR,
    store_code   VARCHAR,
    listed_on    VARCHAR,                        -- 'MM/DD/YYYY'
    delisted_on  VARCHAR,
    status       VARCHAR,                        -- LISTED | SOLD | DELISTED
    price_cents  BIGINT
);
