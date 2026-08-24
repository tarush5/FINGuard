-- Merchant master with observed fraud aggregates.
select
    id                     as merchant_id,
    name                   as merchant_name,
    category,
    mcc,
    country,
    city,
    transaction_count,
    transaction_volume,
    fraud_count,
    fraud_rate,
    chargeback_rate,
    avg_ticket,
    risk_score,
    risk_band,
    high_risk_flag
from {{ source('finguard', 'merchants') }}
where not is_deleted
