-- severity: warning
-- description: Each payout's net = gross - fees - refunds, and matches sum(order total - marketplace fees) - refunds for its channel and period (tolerance max($1, 1% of gross)).
WITH p AS (SELECT * FROM fct_payouts),
ord AS (
    SELECT p.payout_key, sum(o.total) AS order_gross, sum(coalesce(o.marketplace_fees, 0)) AS order_fees, count(*) AS orders
    FROM p JOIN fct_orders o ON o.channel = p.channel AND o.business_date BETWEEN p.period_start AND p.period_end
    GROUP BY p.payout_key
),
ref AS (
    SELECT p.payout_key, sum(r.amount) AS refunds
    FROM p JOIN fct_refunds r ON r.channel = p.channel AND r.business_date BETWEEN p.period_start AND p.period_end
    GROUP BY p.payout_key
),
cmp AS (
    SELECT p.payout_key, p.channel, p.period_start, p.period_end, p.gross, p.fees, p.refunds, p.net,
           coalesce(ord.orders, 0) AS orders,
           coalesce(ord.order_gross, 0) - coalesce(ord.order_fees, 0) - coalesce(ref.refunds, 0) AS expected_net,
           greatest(1.00, 0.01 * abs(coalesce(p.gross, 0))) AS tolerance
    FROM p LEFT JOIN ord USING (payout_key) LEFT JOIN ref USING (payout_key)
)
SELECT CASE WHEN abs(coalesce(net, 0) - (coalesce(gross, 0) - coalesce(fees, 0) - coalesce(refunds, 0))) > 0.01
            THEN 'payout_net_not_gross_minus_fees_refunds' ELSE 'payout_vs_orders' END AS reason,
       payout_key, channel, period_start, period_end, orders, gross, fees, refunds, net, expected_net,
       net - expected_net AS diff, tolerance
FROM cmp
WHERE abs(coalesce(net, 0) - (coalesce(gross, 0) - coalesce(fees, 0) - coalesce(refunds, 0))) > 0.01
   OR abs(coalesce(net, 0) - expected_net) > tolerance
