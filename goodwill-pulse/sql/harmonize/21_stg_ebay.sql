-- 21_stg_ebay.sql : eBay Sell APIs -> staging. ONE seller account shared by Upright (general merch) and
-- Cash Monkey (books); the SKU prefix is the only way to tell them apart (resolved in 30_fct_orders).
-- Quirks: every money value is TEXT ("45.00") -> money_txt(); timestamps are ISO UTC text with millis -> iso_ts().
-- Column layout: see header of 20_stg_amazon.sql.

-- Lines: duplicate API rows come back with a new lineItemId; one physical item (SKU) sells once per order.
CREATE OR REPLACE TEMP TABLE stg_ebay_lines AS
WITH dedup AS (
    SELECT * FROM ebay.line_items
    QUALIFY row_number() OVER (PARTITION BY orderId, coalesce(norm_sku(sku), legacyItemId, lineItemId)
                               ORDER BY lineItemId) = 1
)
SELECT 'ebay' AS channel, orderId AS marketplace_order_id,
       CAST(row_number() OVER (PARTITION BY orderId ORDER BY lineItemId) AS INTEGER) AS line_no,
       norm_sku(sku) AS sku, store_from_sku(sku) AS store_native,
       CAST(NULL AS VARCHAR) AS category_native,   -- categoryId is eBay's numeric taxonomy, not ours
       title, coalesce(quantity, 1) AS quantity,
       coalesce(money_txt(lineItemCost), 0) AS sale_price   -- already price x qty
FROM dedup;

-- Transactions deduplicated on content (a re-pulled page of the Finances API repeats rows under new ids).
CREATE OR REPLACE TEMP TABLE stg_ebay_txn AS
SELECT t.*, iso_ts(t.transactionDate) AS txn_at_utc,
       money_txt(t.amount) AS amount_num, coalesce(money_txt(t.totalFeeAmount), 0) AS fee_num
FROM ebay.transactions t
QUALIFY row_number() OVER (PARTITION BY t.orderId, t.transactionType, t.transactionDate, t.amount,
                                        t.totalFeeAmount, t.payoutId ORDER BY t.transactionId) = 1;

-- Orders: creationDate is the paid time for these (immediate-pay) orders. Money from pricingSummary.
-- Fees: totalMarketplaceFee on the order; if eBay omitted it, fall back to the fees on the SALE transactions.
CREATE OR REPLACE TEMP TABLE stg_ebay_orders AS
WITH sale_fees AS (
    SELECT orderId, sum(fee_num) AS fees FROM stg_ebay_txn WHERE transactionType = 'SALE' GROUP BY 1
)
SELECT 'ebay' AS channel, o.orderId AS marketplace_order_id, o.buyer_username AS buyer_native,
       iso_ts(o.creationDate) AS paid_at_utc,
       coalesce(money_txt(o.pricingSummary_priceSubtotal), 0) AS subtotal,
       coalesce(money_txt(o.pricingSummary_deliveryCost), 0)  AS shipping_charged,
       CAST(0 AS DECIMAL(12,2))                                AS handling,  -- eBay has no handling field; Upright folds it into shipping
       coalesce(money_txt(o.pricingSummary_tax), 0)            AS tax,       -- collected & remitted by eBay, reported for completeness
       CAST(coalesce(money_txt(o.totalMarketplaceFee), sf.fees, 0) AS DECIMAL(12,2)) AS marketplace_fees,
       CASE WHEN is_test_buyer(o.buyer_username) THEN 'test' END AS exclude_reason
FROM ebay.orders o
LEFT JOIN sale_fees sf ON sf.orderId = o.orderId;

-- Refunds: REFUND transactions; eBay reports the amount unsigned with bookingEntry DEBIT, so take abs().
CREATE OR REPLACE TEMP TABLE stg_ebay_refunds AS
SELECT 'ebay:' || transactionId AS refund_key, 'ebay' AS channel, orderId AS marketplace_order_id,
       txn_at_utc AS refunded_at_utc, CAST(abs(amount_num) AS DECIMAL(12,2)) AS amount
FROM stg_ebay_txn WHERE transactionType = 'REFUND';

-- Fees: final value fees on SALE transactions (per order) + NON_SALE_CHARGE (account-level: promoted listings,
-- store subscription) whose amount is the charge. SHIPPING_LABEL is postage cost, not a marketplace fee, and is
-- deliberately excluded (fee_type has no shipping value; see report: contract change request).
CREATE OR REPLACE TEMP TABLE stg_ebay_fees AS
SELECT * FROM (
    SELECT 'ebay:' || transactionId AS fee_key, 'ebay' AS channel, orderId AS marketplace_order_id,
           'final_value' AS fee_type, CAST(fee_num AS DECIMAL(12,2)) AS amount, txn_at_utc AS fee_at_utc
    FROM stg_ebay_txn WHERE transactionType = 'SALE'
    UNION ALL
    SELECT 'ebay:' || transactionId, 'ebay', orderId, 'other',
           CAST(abs(coalesce(amount_num,0)) + fee_num AS DECIMAL(12,2)), txn_at_utc
    FROM stg_ebay_txn WHERE transactionType = 'NON_SALE_CHARGE'
) WHERE amount <> 0;

-- Payouts (daily). paid_on = payout date in Eastern (the bank's business day). Period = span of the
-- transactions it settles. gross = SALE net + its fees; net = the payout amount the bank actually received.
CREATE OR REPLACE TEMP TABLE stg_ebay_payouts AS
WITH t AS (
    SELECT payoutId,
           min(et_date(txn_at_utc)) AS period_start, max(et_date(txn_at_utc)) AS period_end,
           sum(CASE WHEN transactionType = 'SALE' THEN coalesce(amount_num,0) + fee_num ELSE 0 END) AS gross,
           sum(CASE WHEN transactionType = 'SALE' THEN fee_num
                    WHEN transactionType = 'NON_SALE_CHARGE' THEN abs(coalesce(amount_num,0)) + fee_num ELSE 0 END) AS fees,
           sum(CASE WHEN transactionType = 'REFUND' THEN abs(coalesce(amount_num,0)) ELSE 0 END) AS refunds
    FROM stg_ebay_txn WHERE payoutId IS NOT NULL GROUP BY 1
)
SELECT 'ebay:' || p.payoutId AS payout_key, 'ebay' AS channel,
       et_date(iso_ts(p.payoutDate)) AS paid_on, t.period_start, t.period_end,
       CAST(t.gross AS DECIMAL(12,2)) AS gross, CAST(t.fees AS DECIMAL(12,2)) AS fees,
       CAST(t.refunds AS DECIMAL(12,2)) AS refunds, money_txt(p.amount) AS net
FROM ebay.payouts p LEFT JOIN t ON t.payoutId = p.payoutId;

-- Listings: a relist gets a new legacyItemId pointing at its parent.
CREATE OR REPLACE TEMP TABLE stg_ebay_listings AS
SELECT 'ebay:' || legacyItemId AS listing_key, 'ebay' AS channel,
       norm_sku(sku) AS sku, store_from_sku(sku) AS store_native, CAST(NULL AS VARCHAR) AS category_native,
       iso_ts(listingStartDate) AS listed_at_utc, iso_ts(listingEndDate) AS ended_at_utc,
       CASE listingStatus WHEN 'ACTIVE' THEN 'active' WHEN 'SOLD' THEN 'sold' WHEN 'ENDED' THEN 'unsold'
                          ELSE lower(listingStatus) END AS status,
       money_txt(price) AS price,
       CASE WHEN NULLIF(trim(relistParentId), '') IS NOT NULL THEN 'ebay:' || trim(relistParentId) END AS relist_of
FROM ebay.listings;
