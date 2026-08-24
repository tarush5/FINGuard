-- Loss accounting: gross loss, prevented loss and the cost of getting it wrong.
-- The cost assumptions mirror the decision engine (app/services/decision.py) so
-- the warehouse and the application cannot disagree about what a mistake costs.
{{ config(materialized='table') }}

{% set cost_false_positive = 500 %}
{% set cost_manual_review = 150 %}

with transactions as (

    select * from {{ ref('stg_transactions') }}
    where occurred_at >= current_date - interval '{{ var("fraud_loss_horizon_days") }} days'

),

by_dimension as (

    select
        occurred_date,
        channel,
        payment_method,
        country,
        coalesce(fraud_type, 'NONE') as fraud_type,
        count(*) as transactions,
        sum(case when is_fraud and not was_blocked then amount else 0 end) as gross_fraud_loss,
        sum(case when is_fraud and was_blocked then amount else 0 end) as prevented_fraud,
        sum(case when not is_fraud and was_blocked then 1 else 0 end) as false_positives,
        sum(case when decision = 'MANUAL_REVIEW' then 1 else 0 end) as manual_reviews
    from transactions
    group by 1, 2, 3, 4, 5

)

select
    *,
    false_positives * {{ cost_false_positive }} as false_positive_cost,
    manual_reviews * {{ cost_manual_review }} as investigation_cost,
    gross_fraud_loss
        + false_positives * {{ cost_false_positive }}
        + manual_reviews * {{ cost_manual_review }} as net_loss
from by_dimension
order by occurred_date desc, net_loss desc
