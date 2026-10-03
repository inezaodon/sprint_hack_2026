-- severity: warning
-- description: Each payout is internally consistent (net = gross - fees - refunds) and its gross, fees and refunds match the channel's orders, fct_fees (all fee types incl. shipping labels) and refunds for its period (gross basis may be item subtotal, subtotal + shipping + handling, or order total; tolerance max($1, 1%)).
WITH p AS (SELECT * FROM fct_payouts),
ord AS (
    SELECT p.payout_key, count(*) AS orders,
           sum(o.subtotal) AS g_subtotal,
           sum(o.subtotal + coalesce(o.shipping_charged, 0) + coalesce(o.handling, 0)) AS g_with_shipping,
           sum(o.total) AS g_total,
           sum(coalesce(o.marketplace_fees, 0)) AS order_fees
    FROM p JOIN fct_orders o ON o.channel = p.channel AND o.business_date BETWEEN p.period_start AND p.period_end
    GROUP BY p.payout_key
),
fee AS (
    SELECT p.payout_key, sum(f.amount) AS ledger_fees
    FROM p JOIN fct_fees f ON f.channel = p.channel AND f.business_date BETWEEN p.period_start AND p.period_end
    GROUP BY p.payout_key
),
ref AS (
    SELECT p.payout_key, sum(r.amount) AS refunds
    FROM p JOIN fct_refunds r ON r.channel = p.channel AND r.business_date BETWEEN p.period_start AND p.period_end
    GROUP BY p.payout_key
),
cmp AS (
    SELECT p.payout_key, p.channel, p.period_start, p.period_end, p.gross, p.fees, p.refunds, p.net,
           coalesce(ord.orders, 0) AS orders, coalesce(ord.g_subtotal, 0) AS g_subtotal,
           coalesce(ord.g_with_shipping, 0) AS g_with_shipping, coalesce(ord.g_total, 0) AS g_total,
           coalesce(ord.order_fees, 0) AS order_fees, coalesce(fee.ledger_fees, 0) AS ledger_fees,
           coalesce(ref.refunds, 0) AS order_refunds,
           greatest(1.00, 0.01 * abs(coalesce(p.gross, 0))) AS tol
    FROM p LEFT JOIN ord USING (payout_key) LEFT JOIN fee USING (payout_key) LEFT JOIN ref USING (payout_key)
),
flags AS (
    SELECT *,
        abs(coalesce(net, 0) - (coalesce(gross, 0) - coalesce(fees, 0) - coalesce(refunds, 0))) > 0.01 AS bad_net,
        least(abs(coalesce(gross, 0) - g_subtotal), abs(coalesce(gross, 0) - g_with_shipping),
              abs(coalesce(gross, 0) - g_total)) > tol AS bad_gross,
        least(abs(coalesce(fees, 0) - order_fees), abs(coalesce(fees, 0) - ledger_fees)) > tol AS bad_fees,
        abs(coalesce(refunds, 0) - order_refunds) > tol AS bad_refunds
    FROM cmp
)
SELECT concat_ws(', ', CASE WHEN bad_net THEN 'net_not_gross_minus_fees_refunds' END,
                       CASE WHEN bad_gross THEN 'gross_vs_orders' END,
                       CASE WHEN bad_fees THEN 'fees_vs_orders' END,
                       CASE WHEN bad_refunds THEN 'refunds_vs_refund_table' END) AS reason,
       payout_key, channel, period_start, period_end, orders, gross, g_subtotal, g_with_shipping, g_total,
       fees, order_fees, ledger_fees, refunds, order_refunds, net, tol
FROM flags
WHERE bad_net OR bad_gross OR bad_fees OR bad_refunds
