-- 34_fct_payouts.sql : deposits into 1st Source acct 0101, one row per marketplace payout/statement.
INSERT INTO fct_payouts
SELECT payout_key, channel, paid_on, period_start, period_end, gross, fees, refunds, net
FROM (
    SELECT * FROM stg_amazon_payouts
    UNION ALL BY NAME SELECT * FROM stg_ebay_payouts
    UNION ALL BY NAME SELECT * FROM stg_shopgoodwill_payouts
    UNION ALL BY NAME SELECT * FROM stg_goodwillfinds_payouts
    UNION ALL BY NAME SELECT * FROM stg_goodwillbooks_payouts
)
QUALIFY row_number() OVER (PARTITION BY payout_key ORDER BY paid_on) = 1
ORDER BY paid_on, payout_key;
