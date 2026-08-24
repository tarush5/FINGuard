"""Database bootstrap and demo seeding.

``python -m app.datagen.seed`` builds a complete, self-consistent platform:
roles and users, the starter rule pack, governance policies, the data catalogue
and lineage graph, a synthetic portfolio, a scored transaction history, detected
fraud rings, an alert/case backlog with analyst feedback, quality check results
and pipeline run history.

Everything it writes is derived from the generated data -- there are no
hard-coded KPI values anywhere in the platform.
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.rbac import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS, Permission, Role
from app.core.security import hash_password
from app.datagen.backfill import backfill
from app.datagen.generator import GeneratorConfig, SyntheticDataGenerator
from app.db.base import new_id, utcnow
from app.db.models.core import Account, Customer, Device, Merchant, Transaction
from app.db.models.identity import Permission as PermissionRow
from app.db.models.identity import Policy, RoleRecord, User
from app.db.models.platform import Dataset, LineageEdge, PipelineRun
from app.db.models.risk import Rule
from app.db.session import create_all, engine, session_scope
from app.services import cases as case_service
from app.services import graph as graph_service
from app.services import quality as quality_service
from app.services.risk import band_for
from app.utils import clamp, safe_float

logger = get_logger(__name__)

DEMO_PASSWORD = "FinGuard#2026"

DEMO_USERS: list[tuple[str, str, Role, str]] = [
    ("admin@finguard.io", "Avani Kulkarni", Role.ADMIN, "Platform Engineering"),
    ("risk.analyst@finguard.io", "Rohan Mehta", Role.RISK_ANALYST, "Risk Strategy"),
    ("investigator@finguard.io", "Priya Nair", Role.FRAUD_INVESTIGATOR, "Fraud Operations"),
    ("investigator2@finguard.io", "Imran Khan", Role.FRAUD_INVESTIGATOR, "Fraud Operations"),
    ("scientist@finguard.io", "Devansh Rao", Role.DATA_SCIENTIST, "Decision Science"),
    ("engineer@finguard.io", "Meera Iyer", Role.DATA_ENGINEER, "Data Platform"),
    ("exec@finguard.io", "Sanjay Verma", Role.EXECUTIVE, "Executive"),
    ("auditor@finguard.io", "Farah Sheikh", Role.AUDITOR, "Internal Audit"),
]

# --------------------------------------------------------------------- rules

STARTER_RULES: list[dict[str, Any]] = [
    {
        "code": "R-AMT-001",
        "name": "Amount far above customer average",
        "description": "Transaction is more than 5x the customer's historical average spend.",
        "category": "AMOUNT",
        "severity": "HIGH",
        "risk_points": 20,
        "action": "SCORE",
        "priority": 10,
        "condition": {
            "all": [
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 5},
                {"field": "customer_txn_count", "op": "gte", "value": 5},
            ]
        },
    },
    {
        "code": "R-AMT-002",
        "name": "Amount exceeds 10x customer maximum",
        "description": "Amount dwarfs anything this customer has ever transacted.",
        "category": "AMOUNT",
        "severity": "CRITICAL",
        "risk_points": 30,
        "action": "REVIEW",
        "priority": 5,
        "condition": {
            "all": [
                {"field": "amount_to_max_ratio", "op": "gt", "value": 10},
                {"field": "customer_txn_count", "op": "gte", "value": 10},
            ]
        },
    },
    {
        "code": "R-VEL-001",
        "name": "Burst velocity in 5 minutes",
        "description": "More than 8 transactions from the same customer within five minutes.",
        "category": "VELOCITY",
        "severity": "HIGH",
        "risk_points": 25,
        "action": "SCORE",
        "priority": 10,
        "condition": {"field": "txn_count_5m", "op": "gt", "value": 8},
    },
    {
        "code": "R-VEL-002",
        "name": "Sustained hourly velocity",
        "description": "More than 20 transactions in the last hour.",
        "category": "VELOCITY",
        "severity": "MEDIUM",
        "risk_points": 15,
        "action": "SCORE",
        "priority": 20,
        "condition": {"field": "txn_count_1h", "op": "gt", "value": 20},
    },
    {
        "code": "R-CRD-001",
        "name": "Card testing pattern",
        "description": "Several very small authorisations in quick succession.",
        "category": "VELOCITY",
        "severity": "HIGH",
        "risk_points": 22,
        "action": "SCORE",
        "priority": 10,
        "condition": {
            "all": [
                {"field": "txn_count_5m", "op": "gte", "value": 4},
                {"field": "amount", "op": "lt", "value": 150},
            ]
        },
    },
    {
        "code": "R-DEV-001",
        "name": "New device with high value",
        "description": "First time this device is seen for the customer and the amount is large.",
        "category": "DEVICE",
        "severity": "HIGH",
        "risk_points": 18,
        "action": "SCORE",
        "priority": 15,
        "condition": {
            "all": [
                {"field": "is_new_device", "op": "is_true"},
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 2.5},
            ]
        },
    },
    {
        "code": "R-DEV-002",
        "name": "Device shared across accounts",
        "description": "Device fingerprint is linked to three or more distinct customers.",
        "category": "GRAPH",
        "severity": "CRITICAL",
        "risk_points": 24,
        "action": "SCORE",
        "priority": 8,
        "condition": {"field": "device_customer_count", "op": "gte", "value": 3},
    },
    {
        "code": "R-IP-001",
        "name": "IP shared across accounts",
        "description": "Originating IP has been used by four or more accounts in 30 days.",
        "category": "GRAPH",
        "severity": "HIGH",
        "risk_points": 18,
        "action": "SCORE",
        "priority": 15,
        "condition": {"field": "ip_customer_count", "op": "gte", "value": 4},
    },
    {
        "code": "R-GEO-001",
        "name": "Impossible travel",
        "description": "Implied travel speed between consecutive transactions exceeds 900 km/h.",
        "category": "GEOGRAPHY",
        "severity": "CRITICAL",
        "risk_points": 35,
        "action": "REVIEW",
        "priority": 5,
        "condition": {"field": "impossible_travel", "op": "is_true"},
    },
    {
        "code": "R-ATO-001",
        "name": "Account takeover signature",
        "description": (
            "Impossible travel on a brand new device with an amount well above the "
            "customer profile -- blocked outright rather than queued for review."
        ),
        "category": "GEOGRAPHY",
        "severity": "CRITICAL",
        "risk_points": 45,
        "action": "DECLINE",
        "priority": 1,
        "condition": {
            "all": [
                {"field": "impossible_travel", "op": "is_true"},
                {"field": "is_new_device", "op": "is_true"},
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 3},
            ]
        },
    },
    {
        "code": "R-GEO-002",
        "name": "Country change on a new device",
        "description": "Country differs from the previous transaction and the device is new.",
        "category": "GEOGRAPHY",
        "severity": "HIGH",
        "risk_points": 20,
        "action": "SCORE",
        "priority": 12,
        "condition": {
            "all": [
                {"field": "country_change", "op": "is_true"},
                {"field": "is_new_device", "op": "is_true"},
            ]
        },
    },
    {
        "code": "R-MER-001",
        "name": "High-risk merchant with elevated amount",
        "description": "Merchant fraud rate above 3% combined with an above-profile amount.",
        "category": "MERCHANT",
        "severity": "HIGH",
        "risk_points": 16,
        "action": "SCORE",
        "priority": 18,
        "condition": {
            "all": [
                {"field": "merchant_fraud_rate", "op": "gt", "value": 0.03},
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 2},
            ]
        },
    },
    {
        "code": "R-WCH-001",
        "name": "Watchlisted customer activity",
        "description": "Any activity from a customer already flagged for confirmed fraud.",
        "category": "WATCHLIST",
        "severity": "CRITICAL",
        "risk_points": 28,
        "action": "REVIEW",
        "priority": 3,
        "condition": {"field": "customer_watchlisted", "op": "is_true"},
    },
    {
        "code": "R-NGT-001",
        "name": "Night-time spend at an unfamiliar merchant",
        "description": "Between midnight and 06:00 at a merchant the customer has never used.",
        "category": "BEHAVIOUR",
        "severity": "MEDIUM",
        "risk_points": 10,
        "action": "SCORE",
        "priority": 30,
        "condition": {
            "all": [
                {"field": "is_night", "op": "is_true"},
                {"field": "is_new_merchant_for_customer", "op": "is_true"},
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 1.5},
            ]
        },
    },
    {
        "code": "R-CAT-001",
        "name": "Category outside customer profile",
        "description": "Merchant category the customer does not normally transact in.",
        "category": "BEHAVIOUR",
        "severity": "LOW",
        "risk_points": 8,
        "action": "SCORE",
        "priority": 40,
        "condition": {
            "all": [
                {"field": "category_mismatch", "op": "is_true"},
                {"field": "amount_ratio_to_avg", "op": "gt", "value": 2},
            ]
        },
    },
    {
        "code": "R-SHD-001",
        "name": "[Shadow] Aggressive velocity threshold",
        "description": (
            "Shadow rule: evaluated and logged but excluded from scoring, so its "
            "hit rate can be measured before activation."
        ),
        "category": "VELOCITY",
        "severity": "MEDIUM",
        "risk_points": 30,
        "action": "SCORE",
        "priority": 50,
        "is_shadow": True,
        "condition": {"field": "txn_count_5m", "op": "gte", "value": 3},
    },
]

POLICIES: list[dict[str, Any]] = [
    {
        "key": "pii.masking",
        "name": "PII masking by role",
        "category": "PRIVACY",
        "description": "Email, phone, national id and IP are masked for every role except ADMIN and FRAUD_INVESTIGATOR.",
        "config": {
            "unmasked_roles": ["ADMIN", "FRAUD_INVESTIGATOR"],
            "fields": ["email", "phone", "national_id", "ip_address"],
        },
    },
    {
        "key": "ai.sql.readonly",
        "name": "AI SQL is read-only",
        "category": "AI_GOVERNANCE",
        "description": "Generated SQL is parsed and rejected unless it is a single SELECT; DDL/DML keywords are blocked.",
        "config": {
            "blocked": [
                "INSERT",
                "UPDATE",
                "DELETE",
                "DROP",
                "ALTER",
                "TRUNCATE",
                "GRANT",
                "CREATE",
            ],
            "row_limit": settings.ai_sql_row_limit,
        },
    },
    {
        "key": "model.promotion.approval",
        "name": "Model promotion requires model:promote",
        "category": "MLOPS",
        "description": "Only DATA_SCIENTIST and ADMIN may promote a model version to production; every promotion is audited.",
        "config": {"roles": ["DATA_SCIENTIST", "ADMIN"], "audit": True},
    },
    {
        "key": "case.sla",
        "name": "Investigation SLA",
        "category": "OPERATIONS",
        "description": "Critical cases must be actioned within 2 hours, high within 8, medium within 24.",
        "config": {"CRITICAL": 2, "HIGH": 8, "MEDIUM": 24, "LOW": 72},
    },
    {
        "key": "data.retention",
        "name": "Retention schedule",
        "category": "DATA_GOVERNANCE",
        "description": "Transactions retained 7 years, feature vectors 24 months, AI query logs 12 months.",
        "config": {"transactions_months": 84, "features_months": 24, "ai_queries_months": 12},
    },
    {
        "key": "decision.thresholds",
        "name": "Decision thresholds",
        "category": "RISK_POLICY",
        "description": "Score bands that map an ensemble risk score onto an action.",
        "config": {
            "approve_below": settings.decision_approve_below,
            "step_up_below": settings.decision_stepup_below,
            "review_below": settings.decision_review_below,
        },
    },
]

DATASETS: list[dict[str, Any]] = [
    ("transactions_raw", "raw", "Kafka topic transactions.raw landed as-is.", True, "streaming"),
    (
        "transactions_validated",
        "bronze",
        "Schema-validated, deduplicated transactions.",
        True,
        "streaming",
    ),
    (
        "transactions_enriched",
        "silver",
        "Transactions joined to customer, merchant and device context.",
        True,
        "streaming",
    ),
    (
        "transaction_features",
        "silver",
        "Point-in-time feature vectors used for scoring and training.",
        False,
        "streaming",
    ),
    ("fraud_predictions", "gold", "Model inferences with explanations.", False, "streaming"),
    ("risk_scores", "gold", "Ensemble risk breakdown per transaction.", False, "streaming"),
    ("decisions", "gold", "Immutable decision records.", False, "streaming"),
    ("cases", "gold", "Investigation cases and outcomes.", True, "event-driven"),
    ("customers", "silver", "Customer master with behavioural profile.", True, "hourly"),
    ("merchants", "silver", "Merchant master with risk aggregates.", False, "hourly"),
]

LINEAGE: list[tuple[str, str, str, str]] = [
    (
        "transactions_raw",
        "transactions_validated",
        "schema validation + deduplication",
        "validation-consumer",
    ),
    (
        "transactions_validated",
        "transactions_enriched",
        "join customer / merchant / device",
        "enrichment-consumer",
    ),
    (
        "transactions_enriched",
        "transaction_features",
        "point-in-time feature engineering",
        "feature-service",
    ),
    ("transaction_features", "fraud_predictions", "gradient boosted inference", "fraud-model"),
    ("transaction_features", "risk_scores", "ensemble blending", "risk-engine"),
    ("fraud_predictions", "risk_scores", "model component of the ensemble", "risk-engine"),
    ("risk_scores", "decisions", "threshold policy evaluation", "decision-engine"),
    ("decisions", "cases", "case creation for review/decline", "case-service"),
    ("cases", "transaction_features", "analyst labels feed retraining", "feedback-loop"),
    ("customers", "transaction_features", "behavioural profile join", "feature-service"),
    ("merchants", "transaction_features", "merchant risk join", "feature-service"),
]


# --------------------------------------------------------------------- seeding


def seed_rbac(db: Session) -> None:
    existing_permissions = {p.code for p in db.execute(select(PermissionRow)).scalars()}
    for permission in Permission:
        if permission.value in existing_permissions:
            continue
        db.add(
            PermissionRow(
                id=new_id("PERM"),
                code=permission.value,
                description=permission.value.replace(":", " ").replace("_", " ").title(),
            )
        )
    db.flush()

    permission_rows = {p.code: p for p in db.execute(select(PermissionRow)).scalars()}
    for role, permissions in ROLE_PERMISSIONS.items():
        record = db.execute(
            select(RoleRecord).where(RoleRecord.name == role.value)
        ).scalar_one_or_none()
        if record is None:
            record = RoleRecord(
                id=new_id("ROLE"), name=role.value, description=ROLE_DESCRIPTIONS.get(role, "")
            )
            db.add(record)
            db.flush()
        record.description = ROLE_DESCRIPTIONS.get(role, record.description)
        record.permissions = [
            permission_rows[p.value] for p in permissions if p.value in permission_rows
        ]
    db.flush()


def seed_users(db: Session, password: str = DEMO_PASSWORD) -> list[User]:
    roles = {r.name: r for r in db.execute(select(RoleRecord)).scalars()}
    created: list[User] = []
    for email, full_name, role, department in DEMO_USERS:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            user = User(
                id=new_id("USR"),
                email=email,
                full_name=full_name,
                hashed_password=hash_password(password),
                department=department,
            )
            db.add(user)
            db.flush()
            created.append(user)
        user.roles = [roles[role.value]] if role.value in roles else []
    db.flush()
    return created


def seed_rules(db: Session) -> int:
    created = 0
    for spec in STARTER_RULES:
        existing = db.execute(select(Rule).where(Rule.code == spec["code"])).scalar_one_or_none()
        if existing is not None:
            continue
        db.add(
            Rule(
                id=new_id("RULE"),
                code=spec["code"],
                name=spec["name"],
                description=spec["description"],
                category=spec["category"],
                severity=spec["severity"],
                condition=spec["condition"],
                risk_points=spec["risk_points"],
                action=spec["action"],
                priority=spec["priority"],
                is_active=True,
                is_shadow=spec.get("is_shadow", False),
                created_by="seed",
            )
        )
        created += 1
    db.flush()
    return created


def seed_policies(db: Session) -> None:
    for spec in POLICIES:
        existing = db.execute(select(Policy).where(Policy.key == spec["key"])).scalar_one_or_none()
        if existing is not None:
            existing.config = spec["config"]
            continue
        db.add(
            Policy(
                id=new_id("POL"),
                key=spec["key"],
                name=spec["name"],
                category=spec["category"],
                description=spec["description"],
                config=spec["config"],
            )
        )
    db.flush()


def seed_catalogue(db: Session) -> None:
    for name, layer, description, pii, cadence in DATASETS:
        dataset = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if dataset is None:
            dataset = Dataset(id=new_id("DS"), name=name)
            db.add(dataset)
        dataset.layer = layer
        dataset.description = description
        dataset.contains_pii = pii
        dataset.classification = "RESTRICTED" if pii else "INTERNAL"
        dataset.refresh_cadence = cadence
        dataset.owner = "Data Platform" if layer in {"raw", "bronze"} else "Decision Science"
        dataset.steward = "Meera Iyer"
        dataset.tags = [layer, "financial-crime"]
    db.flush()

    for source, target, transformation, processor in LINEAGE:
        edge_id = f"{source}->{target}"
        edge = db.get(LineageEdge, edge_id)
        if edge is None:
            edge = LineageEdge(id=edge_id, source=source, target=target)
            db.add(edge)
        edge.transformation = transformation
        edge.processor = processor
    db.flush()


def refresh_dataset_counts(db: Session) -> None:
    from app.db.models.core import TransactionFeature
    from app.db.models.risk import Case, Decision, FraudPrediction, RiskScore

    counts = {
        "transactions_raw": Transaction,
        "transactions_validated": Transaction,
        "transactions_enriched": Transaction,
        "transaction_features": TransactionFeature,
        "fraud_predictions": FraudPrediction,
        "risk_scores": RiskScore,
        "decisions": Decision,
        "cases": Case,
        "customers": Customer,
        "merchants": Merchant,
    }
    for name, model in counts.items():
        dataset = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if dataset is None:
            continue
        rows = int(db.execute(select(func.count()).select_from(model)).scalar_one() or 0)
        dataset.row_count = rows
        dataset.column_count = len(model.__table__.columns)
        dataset.size_bytes = rows * 512
        dataset.last_refreshed_at = utcnow()
    db.flush()


def insert_reference_data(db: Session, reference: dict[str, list[dict[str, Any]]]) -> None:
    now = utcnow()
    db.bulk_insert_mappings(
        Customer,
        [
            {
                "id": c["id"],
                "full_name": c["full_name"],
                "email": c["email"],
                "phone": c["phone"],
                "national_id": c["national_id"],
                "segment": c["segment"],
                "kyc_status": c["kyc_status"],
                "country": c["country"],
                "city": c["city"],
                "home_latitude": c["home_latitude"],
                "home_longitude": c["home_longitude"],
                "onboarded_at": c["onboarded_at"],
                "tenure_days": c["tenure_days"],
                "avg_transaction_amount": 0.0,
                "std_transaction_amount": 0.0,
                "max_transaction_amount": 0.0,
                "transaction_count": 0,
                "lifetime_value": 0.0,
                "distinct_device_count": len(c["device_ids"]),
                "risk_score": 0.0,
                "risk_band": "LOW",
                "attributes": {"preferred_categories": c["preferred_categories"]},
                "created_at": now,
                "updated_at": now,
                "is_deleted": False,
            }
            for c in reference["customers"]
        ],
    )
    db.bulk_insert_mappings(
        Account,
        [
            {**a, "created_at": now, "updated_at": now, "is_deleted": False}
            for a in reference["accounts"]
        ],
    )
    db.bulk_insert_mappings(
        Merchant,
        [
            {
                "id": m["id"],
                "name": m["name"],
                "category": m["category"],
                "mcc": m["mcc"],
                "country": m["country"],
                "city": m["city"],
                "latitude": m["latitude"],
                "longitude": m["longitude"],
                "onboarded_at": m["onboarded_at"],
                "transaction_count": 0,
                "transaction_volume": 0.0,
                "fraud_count": 0,
                "fraud_rate": m["base_fraud_rate"],
                "risk_score": m["risk_score"],
                "risk_band": band_for(m["risk_score"]),
                "risk_category": "HIGH_RISK" if m["high_risk_flag"] else "STANDARD",
                "high_risk_flag": m["high_risk_flag"],
                "attributes": {"base_ticket": m["base_ticket"]},
                "created_at": now,
                "updated_at": now,
                "is_deleted": False,
            }
            for m in reference["merchants"]
        ],
    )
    seen: set[str] = set()
    device_rows = []
    for d in reference["devices"]:
        if d["id"] in seen:
            continue
        seen.add(d["id"])
        device_rows.append(
            {
                "id": d["id"],
                "device_type": d["device_type"],
                "os": d["os"],
                "browser": d["browser"],
                "fingerprint": d["fingerprint"],
                "first_seen_at": now,
                "last_seen_at": now,
                "transaction_count": 0,
                "distinct_customers": 0,
                "distinct_ips": 0,
                "fraud_count": 0,
                "risk_score": 0.3,
                "is_emulator": False,
                "is_blacklisted": False,
                "attributes": {"ring_device": bool(d.get("is_ring_device"))},
                "created_at": now,
                "updated_at": now,
            }
        )
    db.bulk_insert_mappings(Device, device_rows)
    db.flush()


def recompute_aggregates(db: Session) -> dict[str, Any]:
    """Derive customer / merchant / device risk from the scored history."""
    merchants = {m.id: m for m in db.execute(select(Merchant)).scalars()}
    for merchant in merchants.values():
        count = merchant.transaction_count or 0
        merchant.fraud_rate = round((merchant.fraud_count or 0) / count, 5) if count else 0.0
        merchant.avg_ticket = round(
            safe_float(merchant.transaction_volume) / count if count else 0.0, 2
        )
        merchant.chargeback_rate = round(safe_float(merchant.fraud_rate) * 0.6, 5)
        # Risk blends observed fraud rate, category risk and value concentration.
        risk = 100 * clamp(
            0.62 * clamp(safe_float(merchant.fraud_rate) / 0.05)
            + 0.23 * clamp(count / 800)
            + 0.15 * (1.0 if merchant.high_risk_flag else 0.2)
        )
        merchant.risk_score = round(risk, 2)
        merchant.risk_band = band_for(risk)
        merchant.high_risk_flag = risk >= 55 or safe_float(merchant.fraud_rate) >= 0.03
        merchant.risk_category = "HIGH_RISK" if merchant.high_risk_flag else "STANDARD"

    customers = {c.id: c for c in db.execute(select(Customer)).scalars()}
    device_stats = {
        row[0]: (row[1], row[2])
        for row in db.execute(
            select(
                Transaction.customer_id,
                func.count(func.distinct(Transaction.device_id)),
                func.count(func.distinct(Transaction.ip_address)),
            ).group_by(Transaction.customer_id)
        )
    }
    for customer in customers.values():
        devices, _ips = device_stats.get(customer.id, (0, 0))
        customer.distinct_device_count = int(devices or 0)
        fraud_ratio = (
            (customer.confirmed_fraud_count or 0) / customer.transaction_count
            if customer.transaction_count
            else 0.0
        )
        tenure_signal = clamp(1 - (customer.tenure_days or 0) / 900)
        device_signal = clamp((customer.distinct_device_count - 1) / 5)
        volatility = clamp(
            safe_float(customer.std_transaction_amount)
            / max(safe_float(customer.avg_transaction_amount), 1)
            / 2
        )
        risk = 100 * clamp(
            0.46 * clamp(fraud_ratio * 12)
            + 0.18 * device_signal
            + 0.16 * volatility
            + 0.12 * tenure_signal
            + 0.08 * (1.0 if customer.watchlisted else 0.0)
        )
        customer.risk_score = round(risk, 2)
        customer.risk_band = band_for(risk)
        customer.risk_updated_at = utcnow()
        if (customer.confirmed_fraud_count or 0) > 0:
            customer.watchlisted = True

    devices = {d.id: d for d in db.execute(select(Device)).scalars()}
    ip_counts = {
        row[0]: row[1]
        for row in db.execute(
            select(
                Transaction.device_id, func.count(func.distinct(Transaction.ip_address))
            ).group_by(Transaction.device_id)
        )
    }
    for device in devices.values():
        device.distinct_ips = int(ip_counts.get(device.id, 0) or 0)
        fanout = clamp((device.distinct_customers - 1) / 5)
        fraud_signal = clamp((device.fraud_count or 0) / 5)
        device.risk_score = round(clamp(0.55 * fanout + 0.45 * fraud_signal), 4)
        device.is_blacklisted = device.risk_score >= 0.85
    db.flush()
    return {
        "customers": len(customers),
        "merchants": len(merchants),
        "devices": len(devices),
    }


def seed_case_backlog(
    db: Session, *, max_cases: int = 140, rng: random.Random | None = None
) -> dict[str, int]:
    """Create the historical alert/case backlog from the riskiest transactions."""
    rng = rng or random.Random(7)
    cutoff = utcnow() - timedelta(days=45)
    # Cases come from review/decline outcomes; step-ups produce alerts only.
    candidates = list(
        db.execute(
            select(Transaction)
            .where(
                Transaction.occurred_at >= cutoff,
                Transaction.decision.in_(["MANUAL_REVIEW", "DECLINE"]),
            )
            .order_by(Transaction.risk_score.desc())
            .limit(max_cases)
        ).scalars()
    )
    candidates += list(
        db.execute(
            select(Transaction)
            .where(Transaction.occurred_at >= cutoff, Transaction.decision == "STEP_UP")
            .order_by(Transaction.risk_score.desc())
            .limit(80)
        ).scalars()
    )
    analysts = [
        (u.id, u.full_name)
        for u in db.execute(select(User)).scalars()
        if any(r.name in {"FRAUD_INVESTIGATOR", "RISK_ANALYST"} for r in u.roles)
    ]

    created_alerts = created_cases = resolved = 0
    for transaction in candidates:
        from app.db.models.risk import RiskScore

        risk_record = db.execute(
            select(RiskScore).where(RiskScore.transaction_id == transaction.id).limit(1)
        ).scalar_one_or_none()
        top_factors = risk_record.top_factors if risk_record else []
        triggered = risk_record.triggered_rules if risk_record else []
        headline = (
            top_factors[0]["label"] if top_factors else "Ensemble risk above the review threshold"
        )
        alert = case_service.create_alert(
            db,
            transaction=transaction,
            severity=transaction.risk_band,
            title=(
                f"{transaction.risk_band} risk transaction {transaction.currency} "
                f"{float(transaction.amount):,.2f}"
            ),
            description=str(headline),
            triggered_rules=[r.get("code") for r in triggered],
            details={"top_factors": top_factors, "triggered_rules": triggered},
        )
        created_alerts += 1

        if transaction.decision == "STEP_UP":
            continue

        case = case_service.create_case(
            db,
            transaction=transaction,
            alert=alert,
            title=f"{transaction.risk_band} risk on transaction {transaction.id}",
            summary=str(headline),
            risk_band=transaction.risk_band,
            risk_score=safe_float(transaction.risk_score),
            evidence={"top_factors": top_factors, "triggered_rules": triggered},
            tags=[r.get("category") for r in triggered if r.get("category")][:3],
        )
        created_cases += 1

        if analysts:
            user_id, user_name = rng.choice(analysts)
            case_service.assign(
                db, case, user_id=user_id, user_name=user_name, actor="auto-assignment"
            )

        # Resolve a realistic share of the backlog so the feedback loop, rule
        # precision and analyst metrics all have real data behind them.
        roll = rng.random()
        if roll < 0.42:
            verdict = "CONFIRMED_FRAUD" if transaction.is_fraud else "FALSE_POSITIVE"
            if rng.random() < 0.12:  # analysts are not perfect
                verdict = "FALSE_POSITIVE" if verdict == "CONFIRMED_FRAUD" else "CONFIRMED_FRAUD"
            case_service.transition(
                db,
                case,
                status="INVESTIGATING",
                actor=case.assigned_to_name or "system",
                actor_id=case.assigned_to,
            )
            case_service.transition(
                db,
                case,
                status=verdict,
                actor=case.assigned_to_name or "system",
                actor_id=case.assigned_to,
                notes=(
                    "Device and location evidence consistent with account takeover."
                    if verdict == "CONFIRMED_FRAUD"
                    else "Customer verified the purchase; releasing hold and tuning thresholds."
                ),
            )
            resolved += 1
        elif roll < 0.62:
            case_service.transition(
                db,
                case,
                status="INVESTIGATING",
                actor=case.assigned_to_name or "system",
                actor_id=case.assigned_to,
            )
    db.flush()
    return {"alerts": created_alerts, "cases": created_cases, "resolved": resolved}


def record_pipeline_run(
    db: Session,
    *,
    pipeline: str,
    pipeline_type: str,
    started: float,
    records_in: int,
    records_out: int,
    metrics: dict[str, Any],
    steps: list[dict[str, Any]] | None = None,
    status: str = "SUCCESS",
) -> PipelineRun:
    duration_ms = (time.perf_counter() - started) * 1000
    run = PipelineRun(
        id=new_id("RUN"),
        pipeline=pipeline,
        pipeline_type=pipeline_type,
        run_key=utcnow().strftime("%Y%m%dT%H%M%S"),
        status=status,
        started_at=utcnow() - timedelta(milliseconds=duration_ms),
        finished_at=utcnow(),
        duration_ms=round(duration_ms, 2),
        records_in=records_in,
        records_out=records_out,
        records_failed=max(records_in - records_out, 0),
        triggered_by="seed",
        metrics=metrics,
        steps=steps or [],
    )
    db.add(run)
    db.flush()
    return run


def seed(
    *,
    reset: bool = False,
    customers: int | None = None,
    merchants: int | None = None,
    transactions: int | None = None,
    days: int = 90,
    train: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    """Build the whole demo platform. Returns a summary of what was created."""
    if not quiet:
        configure_logging(json_output=False)

    if reset:
        from app.db.models import Base

        Base.metadata.drop_all(bind=engine)
        logger.info("schema_dropped")
    create_all()

    config = GeneratorConfig(
        customers=customers or settings.seed_customers,
        merchants=merchants or settings.seed_merchants,
        transactions=transactions or settings.seed_transactions,
        fraud_rate=settings.seed_fraud_rate,
        days=days,
        seed=settings.seed_random_state,
    )
    summary: dict[str, Any] = {"config": config.__dict__.copy()}
    overall = time.perf_counter()

    with session_scope() as db:
        existing = int(db.execute(select(func.count()).select_from(Transaction)).scalar_one() or 0)
        if existing and not reset:
            logger.info("seed_skipped_existing_data", extra={"transactions": existing})
            return {"skipped": True, "transactions": existing}

        seed_rbac(db)
        seed_users(db)
        rules_created = seed_rules(db)
        seed_policies(db)
        seed_catalogue(db)
        summary["rules"] = rules_created

    generator = SyntheticDataGenerator(config)
    reference = generator.generate_reference_data()
    raw_transactions = list(generator.generate_transactions())
    summary["generated_transactions"] = len(raw_transactions)

    with session_scope() as db:
        started = time.perf_counter()
        insert_reference_data(db, reference)
        record_pipeline_run(
            db,
            pipeline="reference_data_load",
            pipeline_type="batch",
            started=started,
            records_in=len(reference["customers"]) + len(reference["merchants"]),
            records_out=len(reference["customers"]) + len(reference["merchants"]),
            metrics={
                "customers": len(reference["customers"]),
                "merchants": len(reference["merchants"]),
                "accounts": len(reference["accounts"]),
                "devices": len(reference["devices"]),
            },
        )
        summary["customers"] = len(reference["customers"])
        summary["merchants"] = len(reference["merchants"])

    with session_scope() as db:
        started = time.perf_counter()
        stats = backfill(db, raw_transactions)
        summary["backfill"] = stats.as_dict()
        record_pipeline_run(
            db,
            pipeline="transaction_backfill",
            pipeline_type="streaming-replay",
            started=started,
            records_in=len(raw_transactions),
            records_out=stats.processed,
            metrics=stats.as_dict(),
            steps=[
                {"step": "validate", "status": "SUCCESS"},
                {"step": "features", "status": "SUCCESS"},
                {"step": "rules", "status": "SUCCESS"},
                {"step": "score", "status": "SUCCESS"},
                {"step": "decide", "status": "SUCCESS"},
                {"step": "persist", "status": "SUCCESS"},
            ],
        )

    with session_scope() as db:
        started = time.perf_counter()
        aggregate_summary = recompute_aggregates(db)
        record_pipeline_run(
            db,
            pipeline="risk_aggregates",
            pipeline_type="batch",
            started=started,
            records_in=aggregate_summary["customers"] + aggregate_summary["merchants"],
            records_out=aggregate_summary["customers"] + aggregate_summary["merchants"],
            metrics=aggregate_summary,
        )
        summary["aggregates"] = aggregate_summary

    with session_scope() as db:
        started = time.perf_counter()
        rings = graph_service.detect_rings(db)
        record_pipeline_run(
            db,
            pipeline="fraud_ring_detection",
            pipeline_type="graph",
            started=started,
            records_in=len(rings),
            records_out=len(rings),
            metrics={"rings_detected": len(rings)},
        )
        summary["fraud_rings"] = len(rings)

    with session_scope() as db:
        backlog = seed_case_backlog(db)
        summary["backlog"] = backlog

    if train:
        from app.ml.train import train_all

        with session_scope() as db:
            training = train_all(db, triggered_by="seed", promote=True)
            summary["training"] = training

        with session_scope() as db:
            from app.datagen.backfill import rescore_recent

            started = time.perf_counter()
            rescored = rescore_recent(db, days=10)
            record_pipeline_run(
                db,
                pipeline="model_rescore",
                pipeline_type="batch",
                started=started,
                records_in=rescored.get("rescored", 0),
                records_out=rescored.get("rescored", 0),
                metrics=rescored,
            )
            summary["rescore"] = rescored

        with session_scope() as db:
            from app.ml.drift import compute_drift

            summary["drift"] = compute_drift(db)

    with session_scope() as db:
        started = time.perf_counter()
        quality = quality_service.run_checks(db)
        refresh_dataset_counts(db)
        record_pipeline_run(
            db,
            pipeline="data_quality_suite",
            pipeline_type="batch",
            started=started,
            records_in=len(quality["checks"]),
            records_out=len([c for c in quality["checks"] if c["status"] == "PASS"]),
            metrics={"trust_score": quality["trust_score"], "dimensions": quality["dimensions"]},
            status="SUCCESS" if not quality["failed_checks"] else "WARNING",
        )
        summary["quality"] = {
            "trust_score": quality["trust_score"],
            "failed": len(quality["failed_checks"]),
        }

    summary["duration_seconds"] = round(time.perf_counter() - overall, 2)
    logger.info("seed_completed", extra=summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the FINGuard platform")
    parser.add_argument("--reset", action="store_true", help="drop and recreate every table")
    parser.add_argument("--customers", type=int, default=None)
    parser.add_argument("--merchants", type=int, default=None)
    parser.add_argument("--transactions", type=int, default=None)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--no-train", action="store_true", help="skip model training")
    args = parser.parse_args()

    result = seed(
        reset=args.reset,
        customers=args.customers,
        merchants=args.merchants,
        transactions=args.transactions,
        days=args.days,
        train=not args.no_train,
    )
    print("\nFINGuard seed summary")
    print("-" * 60)
    for key, value in result.items():
        print(f"{key:24s} {value}")


if __name__ == "__main__":
    main()
