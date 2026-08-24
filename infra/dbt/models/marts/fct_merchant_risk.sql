-- Merchant risk mart: the stored risk profile joined to observed behaviour in
-- the analysis window, so a drifting merchant is visible before its profile
-- catches up.
{{ config(materialized='table') }}

with transactions as (

    select * from {{ ref('stg_transactions') }}

),

merchants as (

    select * from {{ ref('stg_merchants') }}

),

observed as (

    select
        merchant_id,
        count(*)                                        as window_transactions,
        sum(amount)                                     as window_volume,
        avg(amount)                                     as window_average_ticket,
        count(distinct customer_id)                     as distinct_customers,
        sum(case when is_fraud then 1 else 0 end)       as window_fraud_transactions,
        sum(case when is_fraud then amount else 0 end)  as window_fraud_amount,
        sum(case when was_blocked then 1 else 0 end)    as window_blocked,
        avg(risk_score)                                 as window_average_risk
    from transactions
    group by merchant_id

)

select
    m.merchant_id,
    m.merchant_name,
    m.category,
    m.country,
    m.risk_score   as profile_risk_score,
    m.risk_band,
    m.high_risk_flag,
    m.fraud_rate   as lifetime_fraud_rate,
    o.window_transactions,
    o.window_volume,
    o.window_average_ticket,
    o.distinct_customers,
    o.window_fraud_transactions,
    o.window_fraud_amount,
    o.window_blocked,
    o.window_average_risk,
    {{ safe_divide('o.window_fraud_transactions', 'o.window_transactions') }} * 100
        as window_fraud_rate_pct,
    -- Concentration: volume drawn from very few customers is structurally
    -- riskier at the same fraud rate.
    {{ safe_divide('o.window_transactions', 'o.distinct_customers') }}
        as transactions_per_customer
from merchants m
inner join observed o using (merchant_id)
order by o.window_fraud_amount desc
