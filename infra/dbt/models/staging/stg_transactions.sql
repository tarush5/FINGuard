-- Cleaned transaction grain: canonical column names, derived time parts and
-- the boolean flags the marts reuse. One row per transaction.
with source as (

    select * from {{ source('finguard', 'transactions') }}
    where occurred_at >= current_date - interval '{{ var("lookback_days") }} days'

),

renamed as (

    select
        id                              as transaction_id,
        event_id,
        customer_id,
        merchant_id,
        device_id,
        account_id,
        amount,
        currency,
        occurred_at,
        date_trunc('day', occurred_at)  as occurred_date,
        date_trunc('hour', occurred_at) as occurred_hour,
        extract(dow  from occurred_at)  as day_of_week,
        extract(hour from occurred_at)  as hour_of_day,
        payment_method,
        merchant_category,
        channel,
        country,
        city,
        risk_score,
        risk_band,
        decision,
        fraud_probability,
        anomaly_score,
        graph_risk,
        rule_score,
        model_version,
        processing_ms,
        coalesce(is_fraud, false)                                   as is_fraud,
        is_fraud is null                                            as is_unlabelled,
        decision in ('DECLINE', 'MANUAL_REVIEW')                    as was_blocked,
        fraud_type,
        label_source
    from source

)

select * from renamed
