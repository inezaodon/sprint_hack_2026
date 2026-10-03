-- data/sources/ebay.duckdb : eBay Sell APIs (Fulfillment + Finances + Inventory), ONE seller account shared by
-- Upright (general merchandise) and Cash Monkey (books). eBay does not know which tool listed an item.
-- Native quirks:
--   * ALL money values are TEXT strings like "45.00" (as eBay JSON returns them); cast in the harmonizer
--   * timestamps are ISO-8601 TEXT in UTC with milliseconds ('2026-09-30T23:41:07.000Z')
--   * line of business only from sku prefix: 'UP-<store>-<n>' (Upright, general merch) or 'GWM-<store>-<n>' (Cash Monkey, books)
CREATE TABLE IF NOT EXISTS orders (
    orderId                            VARCHAR PRIMARY KEY,   -- '12-34567-89012'
    legacyOrderId                      VARCHAR,
    creationDate                       VARCHAR NOT NULL,      -- ISO UTC text (= paid time for these orders)
    orderPaymentStatus                 VARCHAR,               -- PAID | FULLY_REFUNDED | PARTIALLY_REFUNDED
    orderFulfillmentStatus             VARCHAR,               -- FULFILLED | NOT_STARTED
    buyer_username                     VARCHAR,
    buyer_state                        VARCHAR,
    pricingSummary_priceSubtotal       VARCHAR,               -- "45.00"
    pricingSummary_deliveryCost        VARCHAR,
    pricingSummary_tax                 VARCHAR,               -- collected & remitted by eBay
    pricingSummary_total               VARCHAR,
    totalMarketplaceFee                VARCHAR,
    currency                           VARCHAR
);
CREATE TABLE IF NOT EXISTS line_items (
    lineItemId        VARCHAR PRIMARY KEY,
    orderId           VARCHAR NOT NULL,
    legacyItemId      VARCHAR,                   -- listing id
    sku               VARCHAR,                   -- 'UP-03-000123' or 'GWM-03-123456'
    title             VARCHAR,
    categoryId        VARCHAR,                   -- eBay numeric category
    quantity          INTEGER,
    lineItemCost      VARCHAR,                   -- "45.00" (price x qty)
    deliveryCost      VARCHAR
);
CREATE TABLE IF NOT EXISTS transactions (
    transactionId     VARCHAR PRIMARY KEY,
    orderId           VARCHAR,                   -- null for payouts / non-sale charges
    transactionType   VARCHAR,                   -- SALE | REFUND | SHIPPING_LABEL | NON_SALE_CHARGE
    transactionDate   VARCHAR,                   -- ISO UTC text
    amount            VARCHAR,                   -- net to seller, "38.12"
    totalFeeAmount    VARCHAR,                   -- fees on this transaction, positive "6.88"
    bookingEntry      VARCHAR,                   -- CREDIT | DEBIT
    payoutId          VARCHAR
);
CREATE TABLE IF NOT EXISTS payouts (
    payoutId          VARCHAR PRIMARY KEY,
    payoutDate        VARCHAR,                   -- ISO UTC text
    amount            VARCHAR,
    payoutStatus      VARCHAR,                   -- SUCCEEDED
    bankLast4         VARCHAR                    -- '0101'
);
-- Listing history (Inventory/Trading): a relist gets a NEW legacyItemId pointing at the previous one
CREATE TABLE IF NOT EXISTS listings (
    legacyItemId      VARCHAR PRIMARY KEY,
    sku               VARCHAR,
    title             VARCHAR,
    categoryId        VARCHAR,
    listingStartDate  VARCHAR,                   -- ISO UTC text
    listingEndDate    VARCHAR,                   -- null while active
    price             VARCHAR,
    quantitySold      INTEGER,
    listingStatus     VARCHAR,                   -- ACTIVE | ENDED | SOLD
    relistParentId    VARCHAR
);
