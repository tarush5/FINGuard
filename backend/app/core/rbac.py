"""Role based access control.

Permissions are ``resource:action`` strings. Roles map to permission sets, and
API routes declare the permission they require -- never the role -- so that the
role/permission matrix can evolve without touching route code.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    RISK_ANALYST = "RISK_ANALYST"
    FRAUD_INVESTIGATOR = "FRAUD_INVESTIGATOR"
    DATA_SCIENTIST = "DATA_SCIENTIST"
    DATA_ENGINEER = "DATA_ENGINEER"
    EXECUTIVE = "EXECUTIVE"
    AUDITOR = "AUDITOR"


class Permission(StrEnum):
    TRANSACTION_READ = "transaction:read"
    TRANSACTION_INGEST = "transaction:ingest"
    CUSTOMER_READ = "customer:read"
    CUSTOMER_PII_READ = "customer:pii_read"
    MERCHANT_READ = "merchant:read"
    RISK_READ = "risk:read"
    RISK_SIMULATE = "risk:simulate"
    RULE_READ = "rule:read"
    RULE_WRITE = "rule:write"
    ALERT_READ = "alert:read"
    CASE_READ = "case:read"
    CASE_WRITE = "case:write"
    CASE_ASSIGN = "case:assign"
    GRAPH_READ = "graph:read"
    ANALYTICS_READ = "analytics:read"
    FORECAST_READ = "forecast:read"
    MODEL_READ = "model:read"
    MODEL_TRAIN = "model:train"
    MODEL_PROMOTE = "model:promote"
    MONITORING_READ = "monitoring:read"
    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    PIPELINE_RUN = "pipeline:run"
    AI_QUERY = "ai:query"
    AI_SQL = "ai:sql"
    GOVERNANCE_READ = "governance:read"
    GOVERNANCE_WRITE = "governance:write"
    AUDIT_READ = "audit:read"
    USER_MANAGE = "user:manage"
    SYSTEM_ADMIN = "system:admin"


_ALL = frozenset(Permission)

_ANALYTICS_READ_SET = {
    Permission.TRANSACTION_READ,
    Permission.CUSTOMER_READ,
    Permission.MERCHANT_READ,
    Permission.RISK_READ,
    Permission.ANALYTICS_READ,
    Permission.FORECAST_READ,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: _ALL,
    Role.RISK_ANALYST: frozenset(
        _ANALYTICS_READ_SET
        | {
            Permission.ALERT_READ,
            Permission.CASE_READ,
            Permission.CASE_WRITE,
            Permission.RULE_READ,
            Permission.RULE_WRITE,
            Permission.RISK_SIMULATE,
            Permission.GRAPH_READ,
            Permission.MODEL_READ,
            Permission.MONITORING_READ,
            Permission.AI_QUERY,
            Permission.AI_SQL,
        }
    ),
    Role.FRAUD_INVESTIGATOR: frozenset(
        _ANALYTICS_READ_SET
        | {
            Permission.ALERT_READ,
            Permission.CASE_READ,
            Permission.CASE_WRITE,
            Permission.CASE_ASSIGN,
            Permission.CUSTOMER_PII_READ,
            Permission.GRAPH_READ,
            Permission.RULE_READ,
            Permission.MODEL_READ,
            Permission.AI_QUERY,
        }
    ),
    Role.DATA_SCIENTIST: frozenset(
        _ANALYTICS_READ_SET
        | {
            Permission.MODEL_READ,
            Permission.MODEL_TRAIN,
            Permission.MODEL_PROMOTE,
            Permission.MONITORING_READ,
            Permission.DATA_READ,
            Permission.GRAPH_READ,
            Permission.RULE_READ,
            Permission.AI_QUERY,
            Permission.AI_SQL,
            Permission.RISK_SIMULATE,
        }
    ),
    Role.DATA_ENGINEER: frozenset(
        {
            Permission.TRANSACTION_READ,
            Permission.TRANSACTION_INGEST,
            Permission.DATA_READ,
            Permission.DATA_WRITE,
            Permission.PIPELINE_RUN,
            Permission.MONITORING_READ,
            Permission.MODEL_READ,
            Permission.ANALYTICS_READ,
            Permission.AI_QUERY,
        }
    ),
    Role.EXECUTIVE: frozenset(
        _ANALYTICS_READ_SET | {Permission.ALERT_READ, Permission.CASE_READ, Permission.AI_QUERY}
    ),
    Role.AUDITOR: frozenset(
        _ANALYTICS_READ_SET
        | {
            Permission.AUDIT_READ,
            Permission.GOVERNANCE_READ,
            Permission.CASE_READ,
            Permission.ALERT_READ,
            Permission.RULE_READ,
            Permission.MODEL_READ,
            Permission.MONITORING_READ,
        }
    ),
}

# Roles allowed to see unmasked PII. Everyone else receives masked values even
# when they are permitted to read the underlying record.
PII_ROLES = frozenset({Role.ADMIN, Role.FRAUD_INVESTIGATOR})

ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.ADMIN: "Full platform administration, user and policy management.",
    Role.RISK_ANALYST: "Tunes rules and thresholds, reviews risk and runs simulations.",
    Role.FRAUD_INVESTIGATOR: "Works alerts and cases end to end, may view unmasked PII.",
    Role.DATA_SCIENTIST: "Trains, evaluates, promotes models and monitors drift.",
    Role.DATA_ENGINEER: "Operates ingestion, pipelines and data quality.",
    Role.EXECUTIVE: "Read-only portfolio, loss and performance reporting.",
    Role.AUDITOR: "Read-only access to audit trail, governance and decisions.",
}


def permissions_for(roles: list[str] | tuple[str, ...]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for raw in roles:
        try:
            role = Role(raw)
        except ValueError:
            continue
        granted |= set(ROLE_PERMISSIONS.get(role, frozenset()))
    return frozenset(granted)


def has_permission(roles: list[str], permission: Permission) -> bool:
    return permission in permissions_for(roles)


def can_view_pii(roles: list[str]) -> bool:
    return any(r in PII_ROLES for r in roles)
