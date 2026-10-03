-- 32_fct_refunds.sql : refunds, dated by when the money went back (not the order date), so a refund that crosses
-- midnight or month end lands in the period it happened. Refunds of excluded (test/canceled) orders are dropped.
INSERT INTO fct_refunds
SELECT r.refund_key,
       r.channel || ':' || r.marketplace_order_id AS order_key,
       r.channel, r.refunded_at_utc, et_date(r.refunded_at_utc) AS business_date, r.amount
FROM refunds_all r
WHERE r.channel || ':' || r.marketplace_order_id IN (SELECT order_key FROM fct_orders)
  AND r.amount <> 0
QUALIFY row_number() OVER (PARTITION BY r.refund_key ORDER BY r.refunded_at_utc) = 1
ORDER BY r.refunded_at_utc, r.refund_key;
