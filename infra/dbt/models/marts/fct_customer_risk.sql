-- Customer risk mart: stored profile plus observed window behaviour, including
-- the structural signals (device, country and merchant spread) that precede a
-- profile score moving.
{{ config(materialized='table') }}

with transactions as (

    select * from {{ ref('stg_transactions') }}

),

customers as (

    select * from {{ ref('stg_customers') }}

),

observed as (

    select
        customer_id,
        count(*)                                     as window_transactions,
        sum(amount)                                  as window_volume,
        max(amount)                                  as window_max_amount,
        count(distinct merchant_id)                  as distinct_merchants,
        count(distinct device_id)                    as distinct_devices,
        count(distinct country)                      as distinct_countries,
        sum(case when is_fraud then 1 else 0 end)    as window_fraud_transactions,
        sum(case when was_blocked then 1 else 0 end) as window_blocked,
        avg(risk_score)                              as window_average_risk,
        max(risk_score)                              as window_peak_risk
    from transactions
    group by customer_id

)

select
    c.customer_id,
    c.segment,
    c.country,
    c.tenure_days,
    c.risk_score as profile_risk_score,
    c.risk_band,
    c.watchlisted,
    c.confirmed_fraud_count,
    c.lifetime_value,
    c.avg_transaction_amount,
    o.window_transactions,
    o.window_volume,
    o.window_max_amount,
    o.distinct_merchants,
    o.distinct_devices,
    o.distinct_countries,
    o.window_fraud_transactions,
    o.window_blocked,
    o.window_average_risk,
    o.window_peak_risk,
    {{ safe_divide('o.window_fraud_transactions', 'o.window_transactions') }} * 100
        as window_fraud_rate_pct,
    case
        when c.watchlisted then 'WATCHLIST'
        when o.window_peak_risk >= 85 then 'CRITICAL'
        when o.window_peak_risk >= 70 then 'HIGH'
        when o.window_peak_risk >= 40 then 'MEDIUM'
        else 'LOW'
    end as window_risk_band
from customers c
inner join observed o using (customer_id)
order by o.window_peak_risk desc
