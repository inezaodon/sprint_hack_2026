-- data/harmonized.duckdb : ONE canonical model built from the 5 marketplace DBs + ops by sql/harmonize/*.sql.
-- Dashboard KPIs, month-end close and reconciliation read ONLY these tables.
-- Conventions: money DECIMAL(12,2) in USD; every *_utc is TIMESTAMPTZ; business_date = date in America/New_York;
-- channel in {shopgoodwill, ebay, amazon, goodwillfinds, goodwillbooks}; store_id in {'Store01'..'Store24'} or NULL
-- (NULL store = data-quality exception, never silently dropped).

CREATE TABLE IF NOT EXISTS dim_store (
    store_id     VARCHAR PRIMARY KEY,
    store_name   VARCHAR,
    city         VARCHAR,
    ai_flagging  BOOLEAN,
    ecom_eye     BOOLEAN
);
CREATE TABLE IF NOT EXISTS dim_channel (
    channel          VARCHAR PRIMARY KEY,
    label            VARCHAR,          -- 'ShopGoodwill'
    pulse_row        VARCHAR,          -- row label in the Daily Pulse (config/channels.yaml)
    listing_tool     VARCHAR           -- upright | cashmonkey | both
);
CREATE TABLE IF NOT EXISTS dim_date (
    date         DATE PRIMARY KEY,
    month_start  DATE,
    year         INTEGER,
    month        INTEGER,
    iso_week     INTEGER,
    weekday      VARCHAR,
    is_weekend   BOOLEAN
);

CREATE TABLE IF NOT EXISTS fct_orders (
    order_key            VARCHAR PRIMARY KEY,   -- '<channel>:<marketplace_order_id>'
    channel              VARCHAR NOT NULL,
    marketplace_order_id VARCHAR NOT NULL,
    source_db            VARCHAR NOT NULL,      -- amazon | ebay | shopgoodwill | goodwillfinds | goodwillbooks
    line_of_business     VARCHAR,               -- general_merch | books | mixed
    buyer_key            VARCHAR,               -- md5(channel || '|' || native buyer id)  (no PII)
    paid_at_utc          TIMESTAMPTZ NOT NULL,
    business_date        DATE NOT NULL,
    item_count           INTEGER,
    subtotal             DECIMAL(12,2),         -- item revenue (Daily Pulse revenue basis)
    shipping_charged     DECIMAL(12,2),
    handling             DECIMAL(12,2),
    tax                  DECIMAL(12,2),
    marketplace_fees     DECIMAL(12,2),         -- positive = cost
    refund_amount        DECIMAL(12,2),         -- positive = given back
    total                DECIMAL(12,2)          -- subtotal + shipping + handling + tax
);
CREATE TABLE IF NOT EXISTS fct_order_lines (
    order_key         VARCHAR NOT NULL,
    line_no           INTEGER NOT NULL,
    channel           VARCHAR,
    sku               VARCHAR,                 -- = ops.upright_items.item_id
    store_id          VARCHAR,                 -- originating store gets the credit
    category          VARCHAR,                 -- canonical category (see dim mapping in harmonize)
    line_of_business  VARCHAR,
    title             VARCHAR,
    quantity          INTEGER,
    sale_price        DECIMAL(12,2),           -- extended (price x qty)
    fee_alloc         DECIMAL(12,2),           -- order fees allocated pro rata to sale_price
    business_date     DATE,
    PRIMARY KEY (order_key, line_no)
);
CREATE TABLE IF NOT EXISTS fct_refunds (
    refund_key        VARCHAR PRIMARY KEY,     -- '<channel>:<native refund id or order id>'
    order_key         VARCHAR,
    channel           VARCHAR,
    refunded_at_utc   TIMESTAMPTZ,
    business_date     DATE,
    amount            DECIMAL(12,2)            -- positive
);
CREATE TABLE IF NOT EXISTS fct_fees (
    fee_key           VARCHAR PRIMARY KEY,
    order_key         VARCHAR,                 -- null for account-level fees
    channel           VARCHAR,
    fee_type          VARCHAR,                 -- final_value | commission | payment | referral | shipping_label | other
    amount            DECIMAL(12,2),           -- positive = cost
    business_date     DATE
);
CREATE TABLE IF NOT EXISTS fct_payouts (
    payout_key        VARCHAR PRIMARY KEY,     -- '<channel>:<native id>'
    channel           VARCHAR,
    paid_on           DATE,                    -- date money lands in 1st Source acct 0101
    period_start      DATE,
    period_end        DATE,
    gross             DECIMAL(12,2),
    fees              DECIMAL(12,2),
    refunds           DECIMAL(12,2),
    net               DECIMAL(12,2)
);
CREATE TABLE IF NOT EXISTS fct_listings (
    listing_key       VARCHAR PRIMARY KEY,     -- '<channel>:<native listing id>'
    channel           VARCHAR,
    sku               VARCHAR,
    store_id          VARCHAR,
    category          VARCHAR,
    line_of_business  VARCHAR,
    listed_at_utc     TIMESTAMPTZ,
    ended_at_utc      TIMESTAMPTZ,             -- null while active
    status            VARCHAR,                 -- active | sold | unsold
    price             DECIMAL(12,2),
    relist_of         VARCHAR                  -- listing_key of the previous listing, if a relist
);
-- copied/cleaned from ops.duckdb
CREATE TABLE IF NOT EXISTS fct_items (
    item_id           VARCHAR PRIMARY KEY,
    store_id          VARCHAR,
    line_of_business  VARCHAR,
    category          VARCHAR,
    identified_at_utc TIMESTAMPTZ,
    flagged_by        VARCHAR,
    manifested_at_utc TIMESTAMPTZ,
    first_listed_at_utc TIMESTAMPTZ,
    first_sold_at_utc TIMESTAMPTZ,             -- from fct_order_lines join
    poster_id         VARCHAR,
    list_minutes      DOUBLE
);
CREATE TABLE IF NOT EXISTS fct_labor (
    employee_id  VARCHAR,
    role         VARCHAR,
    work_date    DATE,
    hours        DECIMAL(6,2),
    cost         DECIMAL(12,2),
    posted       INTEGER,                      -- listings posted that day (operational_productivity)
    PRIMARY KEY (employee_id, work_date)
);
CREATE TABLE IF NOT EXISTS fct_budget (
    month           DATE,
    channel         VARCHAR,
    revenue_budget  DECIMAL(12,2),
    PRIMARY KEY (month, channel)
);
CREATE TABLE IF NOT EXISTS fct_monthly_inputs (
    month                 DATE PRIMARY KEY,
    overhead_allocation   DECIMAL(12,2),
    store_retail_revenue  DECIMAL(12,2)
);
-- lineage + data quality (written by the harmonizer and sql/checks)
CREATE TABLE IF NOT EXISTS harmonize_runs (
    run_id       VARCHAR PRIMARY KEY,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    source_row_counts JSON,                    -- {"amazon.orders": 1234, ...}
    output_row_counts JSON
);
CREATE TABLE IF NOT EXISTS dq_results (
    check_id     VARCHAR,                      -- file name in sql/checks without .sql
    run_at       TIMESTAMPTZ,
    severity     VARCHAR,                      -- error | warning | info
    status       VARCHAR,                      -- pass | fail
    failing_rows BIGINT,
    detail       JSON,                         -- up to 20 example rows
    description  VARCHAR
);
