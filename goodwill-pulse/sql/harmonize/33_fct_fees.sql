-- 33_fct_fees.sql : every fee row (order-level and account-level). Positive = cost; negative = fee credited back.
-- business_date = when the fee was charged; if the source gives no time, the order's business_date.
-- Fees of excluded orders are dropped; account-level fees (no order) are always kept.
INSERT INTO fct_fees
WITH f AS (
    SELECT * FROM stg_amazon_fees
    UNION ALL BY NAME SELECT * FROM stg_ebay_fees
    UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_fees
    UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_fees
    UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_fees
)
SELECT f.fee_key,
       CASE WHEN f.marketplace_order_id IS NOT NULL THEN f.channel || ':' || f.marketplace_order_id END AS order_key,
       f.channel, f.fee_type, f.amount,
       coalesce(et_date(f.fee_at_utc), o.business_date) AS business_date
FROM f
LEFT JOIN fct_orders o ON o.order_key = f.channel || ':' || f.marketplace_order_id
WHERE f.marketplace_order_id IS NULL OR o.order_key IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY f.fee_key ORDER BY f.fee_at_utc) = 1
ORDER BY f.fee_key;
