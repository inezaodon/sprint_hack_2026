-- data/sources/ops.duckdb : internal operations (Upright Lister back office + timekeeping + finance inputs)
-- Not a marketplace: this is where item lifecycle, labor and budget come from.
CREATE TABLE IF NOT EXISTS stores (
    store_id     VARCHAR PRIMARY KEY,            -- 'Store03'
    store_name   VARCHAR,
    city         VARCHAR,
    ai_flagging  BOOLEAN,                        -- 9 of 24
    ecom_eye     BOOLEAN                         -- 2 of 24
);
-- Upright "Manifest items" + identification: one row per item sent to e-commerce
CREATE TABLE IF NOT EXISTS upright_items (
    item_id        VARCHAR PRIMARY KEY,          -- 'UP-03-000123' or 'GWM-03-123456' (same as marketplace SKU)
    store_id       VARCHAR,
    line_of_business VARCHAR,                    -- general_merch | books
    category       VARCHAR,                      -- canonical category
    title          VARCHAR,
    identified_at  TIMESTAMPTZ,                  -- flagged at store
    flagged_by     VARCHAR,                      -- ai | person
    manifest_id    VARCHAR,
    manifested_at  TIMESTAMPTZ,                  -- sent to e-com
    posted_at      TIMESTAMPTZ,                  -- first listed (null = backlog)
    poster_id      VARCHAR,                      -- employee who listed it
    list_minutes   DOUBLE
);
-- Upright Reports > Operational productivity
CREATE TABLE IF NOT EXISTS operational_productivity (
    employee_id   VARCHAR,
    work_date     DATE,
    accepted      INTEGER,
    rejected      INTEGER,
    photographed  INTEGER,
    posted        INTEGER,
    PRIMARY KEY (employee_id, work_date)
);
CREATE TABLE IF NOT EXISTS employees (
    employee_id  VARCHAR PRIMARY KEY,
    role         VARCHAR,                        -- lister | photographer | shipper | manager
    hourly_rate  DECIMAL(8,2),
    hired_on     DATE
);
CREATE TABLE IF NOT EXISTS timeclock (
    employee_id  VARCHAR,
    work_date    DATE,
    hours        DECIMAL(5,2),
    PRIMARY KEY (employee_id, work_date)
);
CREATE TABLE IF NOT EXISTS budget (
    month           DATE,                        -- first of month
    channel         VARCHAR,                     -- canonical channel
    revenue_budget  DECIMAL(12,2),
    PRIMARY KEY (month, channel)
);
CREATE TABLE IF NOT EXISTS monthly_inputs (
    month                 DATE PRIMARY KEY,
    overhead_allocation   DECIMAL(12,2),         -- allocated e-com overhead (manual, finance)
    store_retail_revenue  DECIMAL(12,2)          -- all 24 stores' donated-goods retail sales
);
