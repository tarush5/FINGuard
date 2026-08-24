-- Daily risk fact: one row per day, the table every trend chart reads from.
{{ config(materialized='table') }}

with transactions as (

    select * from {{ ref('stg_transactions') }}

),

daily as (

    select
        occurred_date,
        count(*)                                                        as transactions,
        sum(amount)                                                     as volume,
        avg(amount)                                                     as average_amount,
        count(distinct customer_id)                                     as active_customers,
        count(distinct merchant_id)                                     as active_merchants,

        sum(case when is_fraud then 1 else 0 end)                       as fraud_transactions,
        sum(case when is_fraud then amount else 0 end)                  as fraud_amount,

        sum(case when decision = 'APPROVE'       then 1 else 0 end)     as approvals,
        sum(case when decision = 'STEP_UP'       then 1 else 0 end)     as step_ups,
        sum(case when decision = 'MANUAL_REVIEW' then 1 else 0 end)     as manual_reviews,
        sum(case when decision = 'DECLINE'       then 1 else 0 end)     as declines,

        -- Confusion matrix of the *decision*, not of the raw model score.
        sum(case when is_fraud and was_blocked then 1 else 0 end)       as true_positives,
        sum(case when not is_fraud and was_blocked then 1 else 0 end)   as false_positives,
        sum(case when is_fraud and not was_blocked then 1 else 0 end)   as false_negatives,

        sum(case when is_fraud and was_blocked then amount else 0 end)  as prevented_amount,
        sum(case when is_fraud and not was_blocked then amount else 0 end) as leaked_amount,

        avg(risk_score)                                                 as average_risk_score,
        avg(processing_ms)                                              as average_decision_ms
    from transactions
    group by occurred_date

)

select
    *,
    {{ safe_divide('fraud_transactions', 'transactions') }} * 100 as fraud_rate_pct,
    {{ safe_divide('true_positives', 'true_positives + false_positives') }} as decision_precision,
    {{ safe_divide('true_positives', 'true_positives + false_negatives') }} as decision_recall,
    {{ safe_divide('prevented_amount', 'prevented_amount + leaked_amount') }} as detection_rate
from daily
order by occurred_date
