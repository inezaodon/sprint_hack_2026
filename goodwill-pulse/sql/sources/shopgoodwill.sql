-- data/sources/shopgoodwill.duckdb : ShopGoodwill.com seller back office (auctions; listed via Upright)
-- Native quirks:
--   * ALL timestamps are naive TIMESTAMP in Pacific time (site runs on America/Los_Angeles)
--   * sales has one row per ITEM; an OrderID with several items repeats; ShippingCharged and Handling
--     are carried on the FIRST line of the order only (0 on the others)
--   * store only from SellerItemCode 'UP-<store>-<n>'
--   * statements are by Period: 1 = days 1-10, 2 = days 11-20, 3 = day 21-month end   (team assumption, see close rules)
CREATE TABLE IF NOT EXISTS auctions (
    ItemID           BIGINT PRIMARY KEY,
    SellerItemCode   VARCHAR,                    -- 'UP-03-000123'
    Title            VARCHAR,
    CategoryName     VARCHAR,
    StartTime        TIMESTAMP,                  -- Pacific, naive
    EndTime          TIMESTAMP,                  -- Pacific, naive
    StartingBid      DECIMAL(12,2),
    NumBids          INTEGER,
    HighBid          DECIMAL(12,2),
    Status           VARCHAR,                    -- Open | Sold | Unsold
    RelistOfItemID   BIGINT
);
CREATE TABLE IF NOT EXISTS sales (
    OrderID          BIGINT NOT NULL,
    ItemID           BIGINT NOT NULL,
    BuyerID          VARCHAR,
    BuyerState       VARCHAR,
    PaidDate         TIMESTAMP,                  -- Pacific, naive
    HammerPrice      DECIMAL(12,2),
    ShippingCharged  DECIMAL(12,2),              -- first line of order only
    Handling         DECIMAL(12,2),              -- first line of order only
    SalesTax         DECIMAL(12,2),
    PaymentMethod    VARCHAR,
    Refunded         BOOLEAN,
    RefundAmount     DECIMAL(12,2),
    RefundDate       TIMESTAMP,                  -- Pacific, naive
    PRIMARY KEY (OrderID, ItemID)
);
CREATE TABLE IF NOT EXISTS seller_fees (
    FeeID            BIGINT PRIMARY KEY,
    OrderID          BIGINT,
    ItemID           BIGINT,
    FeeType          VARCHAR,                    -- Commission | PaymentProcessing
    Amount           DECIMAL(12,2),              -- positive
    FeeDate          TIMESTAMP                   -- Pacific, naive
);
CREATE TABLE IF NOT EXISTS periodic_statements (
    StatementID      BIGINT PRIMARY KEY,
    Year             INTEGER,
    Month            INTEGER,
    Period           INTEGER,                    -- 1 | 2 | 3
    PeriodStart      DATE,
    PeriodEnd        DATE,
    GrossSales       DECIMAL(12,2),
    Commission       DECIMAL(12,2),
    PaymentFees      DECIMAL(12,2),
    Refunds          DECIMAL(12,2),
    NetRemit         DECIMAL(12,2),
    RemitDate        DATE
);
