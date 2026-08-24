-- Customer master with the behavioural profile the risk models consume.
select
    id                          as customer_id,
    segment,
    kyc_status,
    country,
    city,
    onboarded_at,
    tenure_days,
    transaction_count,
    avg_transaction_amount,
    std_transaction_amount,
    max_transaction_amount,
    lifetime_value,
    distinct_device_count,
    confirmed_fraud_count,
    chargeback_count,
    risk_score,
    risk_band,
    watchlisted
from {{ source('finguard', 'customers') }}
where not is_deleted
