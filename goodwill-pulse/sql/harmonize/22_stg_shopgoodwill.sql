-- 22_stg_shopgoodwill.sql : ShopGoodwill seller back office -> staging.
-- Quirks: ALL timestamps are naive Pacific (the site runs on America/Los_Angeles) -> pacific_naive();
-- `sales` is one row per ITEM and ShippingCharged/Handling sit on the first line of an order only.
-- Column layout: see header of 20_stg_amazon.sql.

-- Lines: one per sold item. (OrderID, ItemID) is the PK so the same item cannot repeat inside an order.
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_lines AS
WITH s AS (
    SELECT * FROM shopgoodwill.sales
    QUALIFY row_number() OVER (PARTITION BY OrderID, ItemID ORDER BY PaidDate) = 1
)
SELECT 'shopgoodwill' AS channel, CAST(s.OrderID AS VARCHAR) AS marketplace_order_id,
       CAST(row_number() OVER (PARTITION BY s.OrderID ORDER BY s.ItemID) AS INTEGER) AS line_no,
       norm_sku(a.SellerItemCode) AS sku, store_from_sku(a.SellerItemCode) AS store_native,
       a.CategoryName AS category_native, a.Title AS title,
       1 AS quantity,                                  -- auctions sell exactly one item
       coalesce(s.HammerPrice, 0) AS sale_price
FROM s LEFT JOIN shopgoodwill.auctions a ON a.ItemID = s.ItemID;

-- Fee rows deduplicated on content (FeeID is a surrogate; a repeated export row gets a new one).
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_feerows AS
SELECT * FROM shopgoodwill.seller_fees
QUALIFY row_number() OVER (PARTITION BY OrderID, ItemID, FeeType, Amount, FeeDate ORDER BY FeeID) = 1;

-- Orders: roll the item rows up. Shipping/handling are on the first line only, so SUM over lines gives the
-- order value exactly once (MAX would also work, but SUM stays right if SGW ever splits it across lines).
-- Paid time = earliest PaidDate on the order, Pacific -> UTC.
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_orders AS
WITH s AS (
    SELECT * FROM shopgoodwill.sales
    QUALIFY row_number() OVER (PARTITION BY OrderID, ItemID ORDER BY PaidDate) = 1
), f AS (
    SELECT OrderID, sum(Amount) AS fees FROM stg_shopgoodwill_feerows WHERE OrderID IS NOT NULL GROUP BY 1
)
SELECT 'shopgoodwill' AS channel, CAST(s.OrderID AS VARCHAR) AS marketplace_order_id,
       min(s.BuyerID) AS buyer_native,
       pacific_naive(min(s.PaidDate)) AS paid_at_utc,
       CAST(sum(coalesce(s.HammerPrice,0))     AS DECIMAL(12,2)) AS subtotal,
       CAST(sum(coalesce(s.ShippingCharged,0)) AS DECIMAL(12,2)) AS shipping_charged,
       CAST(sum(coalesce(s.Handling,0))        AS DECIMAL(12,2)) AS handling,
       CAST(sum(coalesce(s.SalesTax,0))        AS DECIMAL(12,2)) AS tax,
       CAST(coalesce(any_value(f.fees), 0)     AS DECIMAL(12,2)) AS marketplace_fees,
       CASE WHEN bool_or(is_test_buyer(s.BuyerID)) THEN 'test' END AS exclude_reason
FROM s LEFT JOIN f ON f.OrderID = s.OrderID
GROUP BY s.OrderID;

-- Refunds are recorded per item on the sales row. Key = order + item so two refunded items stay two refunds.
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_refunds AS
SELECT 'shopgoodwill:' || OrderID || ':' || ItemID AS refund_key, 'shopgoodwill' AS channel,
       CAST(OrderID AS VARCHAR) AS marketplace_order_id,
       pacific_naive(coalesce(RefundDate, PaidDate)) AS refunded_at_utc,
       CAST(RefundAmount AS DECIMAL(12,2)) AS amount
FROM shopgoodwill.sales
WHERE coalesce(RefundAmount, 0) <> 0 OR coalesce(Refunded, false) AND RefundAmount IS NOT NULL;

-- Fees: Commission (per item) and PaymentProcessing (per order); both positive = cost already.
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_fees AS
SELECT 'shopgoodwill:' || FeeID AS fee_key, 'shopgoodwill' AS channel,
       CAST(OrderID AS VARCHAR) AS marketplace_order_id,
       CASE FeeType WHEN 'Commission' THEN 'commission' WHEN 'PaymentProcessing' THEN 'payment' ELSE 'other' END AS fee_type,
       CAST(Amount AS DECIMAL(12,2)) AS amount, pacific_naive(FeeDate) AS fee_at_utc
FROM stg_shopgoodwill_feerows WHERE coalesce(Amount, 0) <> 0;

-- Payouts: one remittance per statement period (1 = days 1-10, 2 = 11-20, 3 = 21-EOM).
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_payouts AS
SELECT 'shopgoodwill:' || StatementID AS payout_key, 'shopgoodwill' AS channel,
       RemitDate AS paid_on, PeriodStart AS period_start, PeriodEnd AS period_end,
       GrossSales AS gross, CAST(coalesce(Commission,0) + coalesce(PaymentFees,0) AS DECIMAL(12,2)) AS fees,
       Refunds AS refunds, NetRemit AS net
FROM shopgoodwill.periodic_statements;

-- Listings: auctions. A sold auction's price is the hammer price, otherwise the starting bid.
CREATE OR REPLACE TEMP TABLE stg_shopgoodwill_listings AS
SELECT 'shopgoodwill:' || ItemID AS listing_key, 'shopgoodwill' AS channel,
       norm_sku(SellerItemCode) AS sku, store_from_sku(SellerItemCode) AS store_native,
       CategoryName AS category_native,
       pacific_naive(StartTime) AS listed_at_utc,
       CASE WHEN Status <> 'Open' THEN pacific_naive(EndTime) END AS ended_at_utc,  -- EndTime of an open auction is scheduled, not actual
       CASE Status WHEN 'Open' THEN 'active' WHEN 'Sold' THEN 'sold' WHEN 'Unsold' THEN 'unsold' ELSE lower(Status) END AS status,
       CASE WHEN Status = 'Sold' THEN coalesce(HighBid, StartingBid) ELSE StartingBid END AS price,
       CASE WHEN RelistOfItemID IS NOT NULL THEN 'shopgoodwill:' || RelistOfItemID END AS relist_of
FROM shopgoodwill.auctions;
