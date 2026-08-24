"""AI fraud investigation assistant.

The assistant answers in two clearly separated layers:

* **Evidence** -- assembled deterministically from the database (features, rule
  hits, model attributions, graph neighbourhood, customer history). Every item
  carries the record it came from. This layer is always factual.
* **Narrative** -- an optional language-model rendering of that evidence, or a
  deterministic template when no provider is configured. Responses state which
  produced them, so a reader always knows what is fact and what is phrasing.

The model never receives database credentials, never chooses what to retrieve,
and cannot introduce a claim that is not in the evidence bundle.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.core import Customer, Merchant, Transaction
from app.db.models.risk import Case, FraudPrediction, RiskScore, RuleExecution
from app.services import graph as graph_service
from app.services.ai.llm import llm_client, sanitise_prompt
from app.utils import safe_float

logger = get_logger(__name__)


def assemble_evidence(db: Session, transaction: Transaction) -> dict[str, Any]:
    """Collect every fact that bears on one transaction's decision."""
    customer = db.get(Customer, transaction.customer_id)
    merchant = db.get(Merchant, transaction.merchant_id)

    risk_record = db.execute(
        select(RiskScore)
        .where(RiskScore.transaction_id == transaction.id)
        .order_by(RiskScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    prediction = db.execute(
        select(FraudPrediction)
        .where(FraudPrediction.transaction_id == transaction.id)
        .order_by(FraudPrediction.predicted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    rule_hits = list(
        db.execute(
            select(RuleExecution).where(
                RuleExecution.transaction_id == transaction.id,
                RuleExecution.triggered.is_(True),
            )
        ).scalars()
    )

    items: list[dict[str, Any]] = []

    amount = safe_float(transaction.amount)
    if customer and safe_float(customer.avg_transaction_amount) > 0:
        ratio = amount / safe_float(customer.avg_transaction_amount)
        items.append(
            {
                "kind": "AMOUNT",
                "source": "customers.avg_transaction_amount",
                "statement": (
                    f"Amount {transaction.currency} {amount:,.2f} is {ratio:.1f}x the customer's "
                    f"average of {transaction.currency} {safe_float(customer.avg_transaction_amount):,.2f}."
                ),
                "value": round(ratio, 2),
                "material": ratio >= 2.0,
            }
        )

    features = (risk_record.components if risk_record else {}) or {}
    feature_values = (prediction.feature_snapshot if prediction else {}) or {}
    if feature_values:
        if feature_values.get("is_new_device"):
            items.append(
                {
                    "kind": "DEVICE",
                    "source": "transaction_features.is_new_device",
                    "statement": f"Device {transaction.device_id} had not been seen for this customer before.",
                    "value": 1,
                    "material": True,
                }
            )
        fan_out = int(feature_values.get("device_customer_count", 1) or 1)
        if fan_out > 1:
            items.append(
                {
                    "kind": "DEVICE",
                    "source": "transaction_features.device_customer_count",
                    "statement": f"The device is linked to {fan_out} distinct customer accounts.",
                    "value": fan_out,
                    "material": fan_out >= 3,
                }
            )
        velocity = int(feature_values.get("txn_count_5m", 0) or 0)
        if velocity:
            items.append(
                {
                    "kind": "VELOCITY",
                    "source": "transaction_features.txn_count_5m",
                    "statement": f"{velocity} transactions from this customer in the preceding five minutes.",
                    "value": velocity,
                    "material": velocity >= 4,
                }
            )
        if feature_values.get("impossible_travel"):
            items.append(
                {
                    "kind": "GEOGRAPHY",
                    "source": "transaction_features.impossible_travel",
                    "statement": (
                        f"Implied travel of {feature_values.get('distance_from_prev_km', 0):,.0f} km "
                        f"in {feature_values.get('seconds_since_prev', 0) / 60:,.0f} minutes is not physically possible."
                    ),
                    "value": 1,
                    "material": True,
                }
            )
        ip_fanout = int(feature_values.get("ip_customer_count", 1) or 1)
        if ip_fanout >= 3:
            items.append(
                {
                    "kind": "NETWORK",
                    "source": "transaction_features.ip_customer_count",
                    "statement": f"The originating IP has been used by {ip_fanout} accounts.",
                    "value": ip_fanout,
                    "material": True,
                }
            )

    if merchant:
        items.append(
            {
                "kind": "MERCHANT",
                "source": "merchants.fraud_rate",
                "statement": (
                    f"Merchant {merchant.name} ({merchant.category}) has a historical fraud rate of "
                    f"{safe_float(merchant.fraud_rate) * 100:.2f}% across {merchant.transaction_count:,} transactions."
                ),
                "value": round(safe_float(merchant.fraud_rate) * 100, 3),
                "material": safe_float(merchant.fraud_rate) >= 0.03,
            }
        )

    if customer and (customer.confirmed_fraud_count or 0) > 0:
        items.append(
            {
                "kind": "HISTORY",
                "source": "customers.confirmed_fraud_count",
                "statement": f"The customer has {customer.confirmed_fraud_count} previously confirmed fraud case(s).",
                "value": customer.confirmed_fraud_count,
                "material": True,
            }
        )

    for hit in rule_hits:
        items.append(
            {
                "kind": "RULE",
                "source": f"rule_executions.{hit.rule_code}",
                "statement": f"Rule {hit.rule_code} triggered, adding {safe_float(hit.risk_points):.0f} risk points.",
                "value": safe_float(hit.risk_points),
                "material": True,
                "matched_values": hit.matched_values,
            }
        )

    graph_result = graph_service.graph_risk(
        db,
        customer_id=transaction.customer_id,
        device_id=transaction.device_id,
        ip_address=transaction.ip_address,
        merchant_id=transaction.merchant_id,
    )
    for signal in graph_result.signals:
        items.append(
            {
                "kind": "GRAPH",
                "source": f"graph.{signal['type']}",
                "statement": signal["detail"],
                "value": signal["weight"],
                "material": True,
                "entities": signal.get("entities", []),
            }
        )

    model_factors = (prediction.explanation or {}).get("top_factors", []) if prediction else []

    return {
        "transaction": {
            "id": transaction.id,
            "amount": amount,
            "currency": transaction.currency,
            "occurred_at": transaction.occurred_at.isoformat() if transaction.occurred_at else None,
            "channel": transaction.channel,
            "payment_method": transaction.payment_method,
            "city": transaction.city,
            "country": transaction.country,
            "decision": transaction.decision,
            "risk_score": safe_float(transaction.risk_score),
            "risk_band": transaction.risk_band,
        },
        "scores": {
            "final_risk": safe_float(transaction.risk_score),
            "fraud_probability": safe_float(transaction.fraud_probability),
            "anomaly_score": safe_float(transaction.anomaly_score),
            "graph_risk": safe_float(transaction.graph_risk),
            "rule_score": safe_float(transaction.rule_score),
            "components": features,
            "model_version": transaction.model_version,
        },
        "model_factors": model_factors[:6],
        "items": items,
        "material_items": [item for item in items if item.get("material")],
        "graph": graph_result.to_dict(),
        "customer_id": transaction.customer_id,
        "merchant_id": transaction.merchant_id,
        "label": {
            "is_fraud": transaction.is_fraud,
            "source": transaction.label_source,
        },
    }


def _deterministic_answer(evidence: dict[str, Any], question: str) -> str:
    """Template narration used when no LLM provider is configured."""
    scores = evidence["scores"]
    txn = evidence["transaction"]
    material = evidence["material_items"]
    lines = [
        f"Assessment: {txn['risk_band']} risk ({scores['final_risk']:.1f}/100), "
        f"decision {txn['decision']}.",
        "",
        "Evidence:",
    ]
    if material:
        lines += [
            f"{index}. {item['statement']}" for index, item in enumerate(material[:8], start=1)
        ]
    else:
        lines.append("1. No individual signal exceeded its materiality threshold.")

    lines += [
        "",
        "Model view:",
        f"- Fraud probability {scores['fraud_probability']:.4f} from {scores['model_version']}.",
        f"- Anomaly score {scores['anomaly_score']:.4f}; graph risk {scores['graph_risk']:.4f}; "
        f"rule score {scores['rule_score']:.1f}.",
    ]
    for factor in evidence["model_factors"][:4]:
        lines.append(
            f"- {factor.get('label')}: value {factor.get('value')}, "
            f"{factor.get('impact_pct', 0)}% of the model's attribution."
        )

    recommendation = {
        "DECLINE": "Keep the block and contact the customer through a verified channel.",
        "MANUAL_REVIEW": "Verify account ownership out-of-band before releasing the hold.",
        "STEP_UP": "Apply step-up authentication and monitor the next 24 hours.",
        "APPROVE": "No action required; continue passive monitoring.",
    }.get(txn["decision"], "Route to a human analyst for a decision.")
    lines += [
        "",
        f"Recommended next step: {recommendation}",
        "",
        "A human analyst makes the final decision.",
    ]
    return "\n".join(lines)


def answer_question(
    db: Session, transaction: Transaction, question: str, *, mask_pii: bool = True
) -> dict[str, Any]:
    """Answer an analyst question about one transaction, grounded in evidence."""
    started = time.perf_counter()
    question = sanitise_prompt(question, max_length=600) or "Why was this transaction flagged?"
    evidence = assemble_evidence(db, transaction)

    narrative = _deterministic_answer(evidence, question)
    generated_by = "deterministic"
    provider_meta: dict[str, Any] = {}

    if llm_client.available:
        response = llm_client.complete(
            system=(
                "Answer the analyst's question about one transaction using only the "
                "evidence JSON. Structure the answer as: Assessment, Evidence "
                "(numbered, each citing its source field), Model factors, Related "
                "entities, Recommended action. Be concise and specific."
            ),
            user=(f"Question: {question}\n\n" f"Evidence JSON:\n{_evidence_for_prompt(evidence)}"),
        )
        if response.text:
            narrative = response.text
            generated_by = "llm"
        provider_meta = {
            "provider": response.provider,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "error": response.error,
        }

    return {
        "question": question,
        "answer": narrative,
        "generated_by": generated_by,
        "provider": provider_meta or llm_client.describe(),
        "evidence": evidence["items"],
        "material_evidence": evidence["material_items"],
        "scores": evidence["scores"],
        "model_factors": evidence["model_factors"],
        "graph": evidence["graph"],
        "transaction": evidence["transaction"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "disclaimer": (
            "Evidence is retrieved from the platform database. The narrative is "
            f"{'model-generated from that evidence' if generated_by == 'llm' else 'template-generated'} "
            "and is advisory only."
        ),
    }


def _evidence_for_prompt(evidence: dict[str, Any]) -> str:
    import json

    trimmed = {
        "transaction": evidence["transaction"],
        "scores": evidence["scores"],
        "model_factors": evidence["model_factors"],
        "evidence_items": evidence["items"][:20],
    }
    return json.dumps(trimmed, default=str, indent=2)[:8000]


def summarise_case(db: Session, case: Case, *, mask_pii: bool = True) -> dict[str, Any]:
    """Produce the case summary shown in the investigation workspace."""
    started = time.perf_counter()
    transaction = (
        db.get(Transaction, case.primary_transaction_id) if case.primary_transaction_id else None
    )
    evidence = (
        assemble_evidence(db, transaction)
        if transaction
        else {
            "items": [],
            "material_items": [],
            "scores": {},
            "model_factors": [],
            "transaction": {},
        }
    )

    indicators = [item["statement"] for item in evidence["material_items"][:6]]
    recommendation = {
        "CRITICAL": "Freeze the account pending verified customer contact and escalate to the fraud desk.",
        "HIGH": "Verify account ownership out-of-band before releasing any held funds.",
        "MEDIUM": "Review the customer's recent activity and confirm the purchase with them.",
        "LOW": "Close as monitoring unless further signals appear.",
    }.get(case.risk_band, "Route to a human analyst.")

    deterministic = "\n".join(
        [
            f"CASE SUMMARY {case.case_number}",
            "",
            f"Risk level: {case.risk_band} ({safe_float(case.risk_score):.1f}/100)",
            f"Exposure: {safe_float(case.exposure_amount):,.2f}",
            f"Status: {case.status}"
            + (f" | Assigned to {case.assigned_to_name}" if case.assigned_to_name else ""),
            "",
            "Primary indicators:",
        ]
        + ([f"- {indicator}" for indicator in indicators] or ["- No material indicator recorded."])
        + ["", f"Recommended next step: {recommendation}"]
    )

    summary = deterministic
    generated_by = "deterministic"
    provider_meta: dict[str, Any] = {}

    if llm_client.available:
        response = llm_client.complete(
            system=(
                "Write a concise fraud case summary for an investigator. Sections: "
                "Risk level, Primary indicators (bullets), What to verify, "
                "Recommended next step. Use only the evidence provided."
            ),
            user=(
                f"Case {case.case_number}: status {case.status}, risk {case.risk_band} "
                f"({safe_float(case.risk_score):.1f}/100), exposure {safe_float(case.exposure_amount):,.2f}.\n\n"
                f"Evidence JSON:\n{_evidence_for_prompt(evidence)}"
            ),
        )
        if response.text:
            summary = response.text
            generated_by = "llm"
        provider_meta = {
            "provider": response.provider,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "error": response.error,
        }

    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "summary": summary,
        "generated_by": generated_by,
        "provider": provider_meta or llm_client.describe(),
        "indicators": indicators,
        "recommended_action": recommendation,
        "evidence": evidence["items"],
        "scores": evidence["scores"],
        "model_factors": evidence["model_factors"],
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
