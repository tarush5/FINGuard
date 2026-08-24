"""Entity serialisers with PII masking applied at the boundary.

Masking happens here -- in the single place every response passes through --
rather than in each route, so a new endpoint cannot accidentally leak PII.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.security import mask_email, mask_ip, mask_pan, mask_phone
from app.db.models.core import (
    Account,
    Customer,
    Device,
    Merchant,
    Transaction,
    TransactionFeature,
)
from app.db.models.risk import Alert, FraudRing, Rule
from app.utils import safe_float


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def serialize_transaction(
    txn: Transaction | None, *, mask_pii: bool = True, features: TransactionFeature | None = None
) -> dict[str, Any] | None:
    if txn is None:
        return None
    should_mask = mask_pii and settings.mask_pii
    payload: dict[str, Any] = {
        "id": txn.id,
        "event_id": txn.event_id,
        "correlation_id": txn.correlation_id,
        "customer_id": txn.customer_id,
        "account_id": txn.account_id,
        "merchant_id": txn.merchant_id,
        "device_id": txn.device_id,
        "amount": safe_float(txn.amount),
        "currency": txn.currency,
        "occurred_at": _iso(txn.occurred_at),
        "ingested_at": _iso(txn.ingested_at),
        "payment_method": txn.payment_method,
        "merchant_category": txn.merchant_category,
        "channel": txn.channel,
        "transaction_type": txn.transaction_type,
        "status": txn.status,
        "ip_address": mask_ip(txn.ip_address) if should_mask else txn.ip_address,
        "latitude": txn.latitude,
        "longitude": txn.longitude,
        "country": txn.country,
        "city": txn.city,
        "session_id": txn.session_id,
        "risk_score": safe_float(txn.risk_score),
        "risk_band": txn.risk_band,
        "decision": txn.decision,
        "fraud_probability": safe_float(txn.fraud_probability),
        "anomaly_score": safe_float(txn.anomaly_score),
        "graph_risk": safe_float(txn.graph_risk),
        "rule_score": safe_float(txn.rule_score),
        "processing_ms": safe_float(txn.processing_ms),
        "model_version": txn.model_version,
        "is_fraud": txn.is_fraud,
        "fraud_type": txn.fraud_type,
        "label_source": txn.label_source,
        "is_demo": txn.is_demo,
    }
    if features is not None:
        payload["features"] = features.features
        payload["feature_version"] = features.feature_version
    return payload


def serialize_customer(
    customer: Customer | None, *, mask_pii: bool = True
) -> dict[str, Any] | None:
    if customer is None:
        return None
    should_mask = mask_pii and settings.mask_pii
    return {
        "id": customer.id,
        "full_name": customer.full_name,
        "email": mask_email(customer.email) if should_mask else customer.email,
        "phone": mask_phone(customer.phone) if should_mask else customer.phone,
        "national_id": mask_pan(customer.national_id) if should_mask else customer.national_id,
        "segment": customer.segment,
        "kyc_status": customer.kyc_status,
        "country": customer.country,
        "city": customer.city,
        "onboarded_at": _iso(customer.onboarded_at),
        "tenure_days": customer.tenure_days,
        "avg_transaction_amount": safe_float(customer.avg_transaction_amount),
        "std_transaction_amount": safe_float(customer.std_transaction_amount),
        "max_transaction_amount": safe_float(customer.max_transaction_amount),
        "transaction_count": customer.transaction_count,
        "lifetime_value": safe_float(customer.lifetime_value),
        "typical_merchant_category": customer.typical_merchant_category,
        "distinct_device_count": customer.distinct_device_count,
        "confirmed_fraud_count": customer.confirmed_fraud_count,
        "chargeback_count": customer.chargeback_count,
        "risk_score": safe_float(customer.risk_score),
        "risk_band": customer.risk_band,
        "watchlisted": customer.watchlisted,
        "pii_masked": should_mask,
    }


def serialize_merchant(merchant: Merchant | None) -> dict[str, Any] | None:
    if merchant is None:
        return None
    return {
        "id": merchant.id,
        "name": merchant.name,
        "category": merchant.category,
        "mcc": merchant.mcc,
        "country": merchant.country,
        "city": merchant.city,
        "latitude": merchant.latitude,
        "longitude": merchant.longitude,
        "onboarded_at": _iso(merchant.onboarded_at),
        "transaction_count": merchant.transaction_count,
        "transaction_volume": safe_float(merchant.transaction_volume),
        "fraud_count": merchant.fraud_count,
        "fraud_rate": safe_float(merchant.fraud_rate),
        "chargeback_rate": safe_float(merchant.chargeback_rate),
        "avg_ticket": safe_float(merchant.avg_ticket),
        "risk_score": safe_float(merchant.risk_score),
        "risk_band": merchant.risk_band,
        "risk_category": merchant.risk_category,
        "high_risk_flag": merchant.high_risk_flag,
    }


def serialize_device(device: Device | None) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "id": device.id,
        "device_type": device.device_type,
        "os": device.os,
        "browser": device.browser,
        "first_seen_at": _iso(device.first_seen_at),
        "last_seen_at": _iso(device.last_seen_at),
        "transaction_count": device.transaction_count,
        "distinct_customers": device.distinct_customers,
        "fraud_count": device.fraud_count,
        "risk_score": safe_float(device.risk_score),
        "is_emulator": device.is_emulator,
        "is_blacklisted": device.is_blacklisted,
    }


def serialize_account(account: Account | None) -> dict[str, Any] | None:
    if account is None:
        return None
    return {
        "id": account.id,
        "customer_id": account.customer_id,
        "account_type": account.account_type,
        "currency": account.currency,
        "masked_number": account.masked_number,
        "balance": safe_float(account.balance),
        "credit_limit": safe_float(account.credit_limit),
        "status": account.status,
        "opened_at": _iso(account.opened_at),
    }


def serialize_alert(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "transaction_id": alert.transaction_id,
        "customer_id": alert.customer_id,
        "merchant_id": alert.merchant_id,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.title,
        "description": alert.description,
        "risk_score": safe_float(alert.risk_score),
        "amount": safe_float(alert.amount),
        "status": alert.status,
        "case_id": alert.case_id,
        "triggered_rules": alert.triggered_rules or [],
        "details": alert.details or {},
        "created_at": _iso(alert.created_at),
    }


def serialize_rule(rule: Rule) -> dict[str, Any]:
    from app.services.rules import describe_condition

    return {
        "id": rule.id,
        "code": rule.code,
        "name": rule.name,
        "description": rule.description,
        "category": rule.category,
        "severity": rule.severity,
        "version": rule.version,
        "condition": rule.condition,
        "condition_text": describe_condition(rule.condition),
        "risk_points": safe_float(rule.risk_points),
        "action": rule.action,
        "priority": rule.priority,
        "is_active": rule.is_active,
        "is_shadow": rule.is_shadow,
        "hit_count": rule.hit_count,
        "true_positive_count": rule.true_positive_count,
        "false_positive_count": rule.false_positive_count,
        "precision": rule.precision,
        "last_triggered_at": _iso(rule.last_triggered_at),
        "created_by": rule.created_by,
        "updated_by": rule.updated_by,
        "created_at": _iso(rule.created_at),
        "updated_at": _iso(rule.updated_at),
    }


def serialize_ring(ring: FraudRing) -> dict[str, Any]:
    return {
        "id": ring.id,
        "label": ring.label,
        "detection_method": ring.detection_method,
        "detected_at": _iso(ring.detected_at),
        "member_count": ring.member_count,
        "transaction_count": ring.transaction_count,
        "total_amount": safe_float(ring.total_amount),
        "fraud_probability": safe_float(ring.fraud_probability),
        "risk_score": safe_float(ring.risk_score),
        "status": ring.status,
        "shared_devices": ring.shared_devices or [],
        "shared_ips": ring.shared_ips or [],
        "shared_merchants": ring.shared_merchants or [],
        "density": safe_float(ring.density),
        "evidence": ring.evidence or {},
        "members": [
            {
                "entity_type": member.entity_type,
                "entity_id": member.entity_id,
                "role_in_ring": member.role_in_ring,
                "centrality": safe_float(member.centrality),
                "risk_contribution": safe_float(member.risk_contribution),
            }
            for member in ring.members
        ],
    }
