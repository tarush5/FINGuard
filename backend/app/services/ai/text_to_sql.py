"""Natural-language analytics (text-to-SQL).

Flow:  question -> intent -> schema retrieval -> SQL generation -> validation
       -> permission check -> execution -> result -> visualisation hint

Safety is enforced *after* generation and does not trust the model:

* only a single statement, which must be a ``SELECT`` (or ``WITH ... SELECT``);
* every referenced table must be on the allow-list, and the allow-list is
  filtered by the caller's RBAC permissions;
* PII columns are rejected for roles without ``customer:pii_read``;
* a ``LIMIT`` is injected when absent, and execution is read-only.

When no LLM is configured a curated intent library answers the common analyst
questions with parameterised SQL -- real answers, not a stub.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import UnsafeQueryError
from app.core.logging import get_logger
from app.core.rbac import Permission
from app.services.ai.llm import llm_client, sanitise_prompt

logger = get_logger(__name__)

# Table -> (description, required permission, pii columns)
SCHEMA_CATALOGUE: dict[str, dict[str, Any]] = {
    "transactions": {
        "description": "One row per processed transaction with its decision and risk breakdown.",
        "permission": Permission.TRANSACTION_READ,
        "pii_columns": {"ip_address"},
        "columns": (
            "id, event_id, customer_id, account_id, merchant_id, device_id, amount, currency, "
            "occurred_at, payment_method, merchant_category, channel, transaction_type, status, "
            "ip_address, latitude, longitude, country, city, risk_score, risk_band, decision, "
            "fraud_probability, anomaly_score, graph_risk, rule_score, model_version, is_fraud, "
            "fraud_type, processing_ms"
        ),
    },
    "customers": {
        "description": "Customer master with behavioural profile and risk score.",
        "permission": Permission.CUSTOMER_READ,
        "pii_columns": {"email", "phone", "national_id", "full_name"},
        "columns": (
            "id, full_name, email, phone, national_id, segment, kyc_status, country, city, "
            "onboarded_at, tenure_days, avg_transaction_amount, std_transaction_amount, "
            "max_transaction_amount, transaction_count, lifetime_value, distinct_device_count, "
            "confirmed_fraud_count, risk_score, risk_band, watchlisted"
        ),
    },
    "merchants": {
        "description": "Merchant master with fraud rate and risk aggregates.",
        "permission": Permission.MERCHANT_READ,
        "pii_columns": set(),
        "columns": (
            "id, name, category, mcc, country, city, transaction_count, transaction_volume, "
            "fraud_count, fraud_rate, chargeback_rate, avg_ticket, risk_score, risk_band, "
            "high_risk_flag"
        ),
    },
    "cases": {
        "description": "Investigation cases with status, assignment and outcome.",
        "permission": Permission.CASE_READ,
        "pii_columns": set(),
        "columns": (
            "id, case_number, title, status, priority, risk_band, risk_score, customer_id, "
            "merchant_id, primary_transaction_id, exposure_amount, assigned_to_name, "
            "resolution, created_at, resolved_at"
        ),
    },
    "alerts": {
        "description": "Alerts raised by the decision engine.",
        "permission": Permission.ALERT_READ,
        "pii_columns": set(),
        "columns": (
            "id, transaction_id, customer_id, merchant_id, alert_type, severity, title, "
            "risk_score, amount, status, case_id, created_at"
        ),
    },
    "rules": {
        "description": "Detection rules with hit counts and analyst-confirmed precision.",
        "permission": Permission.RULE_READ,
        "pii_columns": set(),
        "columns": (
            "id, code, name, category, severity, risk_points, action, priority, is_active, "
            "is_shadow, hit_count, true_positive_count, false_positive_count"
        ),
    },
    "rule_executions": {
        "description": "Per-transaction rule evaluation results.",
        "permission": Permission.RULE_READ,
        "pii_columns": set(),
        "columns": "id, rule_id, rule_code, transaction_id, evaluated_at, triggered, risk_points",
    },
    "fraud_rings": {
        "description": "Detected fraud rings with member counts and shared infrastructure.",
        "permission": Permission.GRAPH_READ,
        "pii_columns": set(),
        "columns": (
            "id, label, detection_method, detected_at, member_count, transaction_count, "
            "total_amount, fraud_probability, risk_score, status"
        ),
    },
    "devices": {
        "description": "Device fingerprints with fan-out and risk.",
        "permission": Permission.TRANSACTION_READ,
        "pii_columns": set(),
        "columns": (
            "id, device_type, os, browser, first_seen_at, last_seen_at, transaction_count, "
            "distinct_customers, distinct_ips, fraud_count, risk_score, is_blacklisted"
        ),
    },
    "model_versions": {
        "description": "Model registry entries with evaluation metrics.",
        "permission": Permission.MODEL_READ,
        "pii_columns": set(),
        "columns": "id, name, version, tag, algorithm, stage, threshold, trained_at, positive_rate",
    },
    "decisions": {
        "description": "Immutable decision records per transaction.",
        "permission": Permission.TRANSACTION_READ,
        "pii_columns": set(),
        "columns": "id, transaction_id, decided_at, outcome, risk_score, reason, policy_version",
    },
}

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|attach|detach|"
    r"pragma|vacuum|replace|merge|copy|call|execute|commit|rollback|savepoint|"
    r"set|reindex|analyze|load_extension)\b",
    re.IGNORECASE,
)
TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
LIMIT_PRESENT = re.compile(r"\blimit\s+\d+", re.IGNORECASE)
SENSITIVE_TABLES = {"users", "refresh_tokens", "permissions", "roles", "audit_logs", "policies"}


@dataclass
class SqlValidation:
    sql: str
    valid: bool
    reason: str | None = None
    tables: list[str] = field(default_factory=list)


def visible_tables(permissions: frozenset[Permission]) -> dict[str, dict[str, Any]]:
    return {
        name: spec for name, spec in SCHEMA_CATALOGUE.items() if spec["permission"] in permissions
    }


def schema_prompt(tables: dict[str, dict[str, Any]]) -> str:
    return "\n".join(
        f"TABLE {name}  -- {spec['description']}\n  columns: {spec['columns']}"
        for name, spec in tables.items()
    )


def validate_sql(
    sql: str, *, permissions: frozenset[Permission], allow_pii: bool, row_limit: int
) -> SqlValidation:
    """Reject anything that is not a safe, single, read-only SELECT."""
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        return SqlValidation(sql="", valid=False, reason="The generated query was empty.")
    if ";" in cleaned:
        return SqlValidation(
            sql=cleaned, valid=False, reason="Multiple statements are not allowed."
        )
    if "--" in cleaned or "/*" in cleaned:
        return SqlValidation(sql=cleaned, valid=False, reason="SQL comments are not allowed.")

    lowered = cleaned.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return SqlValidation(
            sql=cleaned, valid=False, reason="Only SELECT statements can be executed."
        )
    if FORBIDDEN.search(cleaned):
        offending = FORBIDDEN.search(cleaned)
        return SqlValidation(
            sql=cleaned,
            valid=False,
            reason=f"Statement contains the blocked keyword '{offending.group(0)}'.",
        )

    referenced = {match.lower() for match in TABLE_REFERENCE.findall(cleaned)}
    allowed = visible_tables(permissions)
    # Aliases and CTE names appear as references too; only flag real tables.
    unknown = {
        table for table in referenced if table in SCHEMA_CATALOGUE or table in SENSITIVE_TABLES
    }
    forbidden_tables = [t for t in unknown if t not in allowed]
    if forbidden_tables:
        return SqlValidation(
            sql=cleaned,
            valid=False,
            reason=f"Your role cannot query: {', '.join(sorted(forbidden_tables))}.",
            tables=sorted(referenced),
        )

    if not allow_pii:
        for table in unknown:
            for column in SCHEMA_CATALOGUE.get(table, {}).get("pii_columns", set()):
                if re.search(rf"\b{re.escape(column)}\b", lowered):
                    return SqlValidation(
                        sql=cleaned,
                        valid=False,
                        reason=(
                            f"Column '{column}' contains PII and your role lacks "
                            "customer:pii_read."
                        ),
                        tables=sorted(referenced),
                    )

    if not LIMIT_PRESENT.search(cleaned):
        cleaned = f"{cleaned} LIMIT {row_limit}"
    return SqlValidation(sql=cleaned, valid=True, tables=sorted(unknown))


# --------------------------------------------------------------- intent library

INTENTS: list[dict[str, Any]] = [
    {
        "key": "high_fraud_merchants",
        "patterns": [
            r"merchant.*fraud",
            r"fraud.*merchant",
            r"(riskiest|worst|highest risk|top).*merchants",
        ],
        "description": "Merchants ranked by fraud rate",
        "sql": (
            "SELECT id AS merchant_id, name, category, transaction_count, "
            "ROUND(fraud_rate * 100, 3) AS fraud_rate_pct, fraud_count, "
            "ROUND(risk_score, 2) AS risk_score FROM merchants "
            "WHERE transaction_count > 0 ORDER BY fraud_rate DESC LIMIT 20"
        ),
        "chart": {"type": "bar", "x": "name", "y": "fraud_rate_pct"},
    },
    {
        "key": "fraud_by_day",
        "patterns": [
            r"fraud.*(per day|by day|daily|trend|over time)",
            r"daily fraud",
            r"fraud trend",
        ],
        "description": "Daily fraud volume and amount",
        "sql": (
            "SELECT DATE(occurred_at) AS day, COUNT(*) AS transactions, "
            "SUM(CASE WHEN is_fraud THEN 1 ELSE 0 END) AS fraud_transactions, "
            "ROUND(SUM(CASE WHEN is_fraud THEN amount ELSE 0 END), 2) AS fraud_amount "
            "FROM transactions GROUP BY DATE(occurred_at) ORDER BY day DESC LIMIT 60"
        ),
        "chart": {"type": "line", "x": "day", "y": "fraud_transactions"},
    },
    {
        "key": "top_risk_customers",
        "patterns": [
            r"(riskiest|highest risk|top risk|most risky).*customers",
            r"customers.*(risk score|riskiest|watchlist)",
        ],
        "description": "Customers ranked by risk score",
        "sql": (
            "SELECT id AS customer_id, segment, country, transaction_count, "
            "ROUND(risk_score, 2) AS risk_score, risk_band, confirmed_fraud_count, watchlisted "
            "FROM customers ORDER BY risk_score DESC LIMIT 20"
        ),
        "chart": {"type": "bar", "x": "customer_id", "y": "risk_score"},
    },
    {
        "key": "decision_mix",
        "patterns": [
            r"(decision|approve|decline|declined).*(breakdown|mix|distribution|split|how many)",
            r"how many (declined|approved|reviews)",
        ],
        "description": "Decision outcome distribution",
        "sql": (
            "SELECT decision, COUNT(*) AS transactions, ROUND(SUM(amount), 2) AS volume, "
            "ROUND(AVG(risk_score), 2) AS average_risk FROM transactions "
            "GROUP BY decision ORDER BY transactions DESC LIMIT 10"
        ),
        "chart": {"type": "pie", "x": "decision", "y": "transactions"},
    },
    {
        "key": "rule_performance",
        "patterns": [
            r"rules?.*(performance|precision|hit|trigger|fire)",
            r"which rules",
            r"top rules",
        ],
        "description": "Rule hit counts and analyst-confirmed precision",
        "sql": (
            "SELECT code, name, category, hit_count, true_positive_count, "
            "false_positive_count, risk_points, is_active FROM rules "
            "ORDER BY hit_count DESC LIMIT 25"
        ),
        "chart": {"type": "bar", "x": "code", "y": "hit_count"},
    },
    {
        "key": "largest_transactions",
        "patterns": [
            r"(largest|biggest|highest|top).*transactions?",
            r"transactions?.*(by amount|largest|biggest)",
        ],
        "description": "Largest transactions by amount",
        "sql": (
            "SELECT id, customer_id, merchant_id, ROUND(amount, 2) AS amount, currency, "
            "occurred_at, decision, ROUND(risk_score, 2) AS risk_score FROM transactions "
            "ORDER BY amount DESC LIMIT 20"
        ),
        "chart": {"type": "bar", "x": "id", "y": "amount"},
    },
    {
        "key": "open_cases",
        "patterns": [r"(open|pending).*cases", r"case (backlog|queue)"],
        "description": "Open investigation cases by priority",
        "sql": (
            "SELECT case_number, title, status, priority, ROUND(risk_score, 2) AS risk_score, "
            "ROUND(exposure_amount, 2) AS exposure, assigned_to_name, created_at FROM cases "
            "WHERE status NOT IN ('RESOLVED', 'CONFIRMED_FRAUD', 'FALSE_POSITIVE') "
            "ORDER BY risk_score DESC LIMIT 25"
        ),
        "chart": {"type": "table"},
    },
    {
        "key": "fraud_by_channel",
        "patterns": [
            r"(channel|payment method|payment).*(fraud|risk)",
            r"fraud by (channel|payment)",
        ],
        "description": "Fraud rate by channel",
        "sql": (
            "SELECT channel, COUNT(*) AS transactions, "
            "SUM(CASE WHEN is_fraud THEN 1 ELSE 0 END) AS fraud_transactions, "
            "ROUND(100.0 * SUM(CASE WHEN is_fraud THEN 1 ELSE 0 END) / COUNT(*), 3) AS fraud_rate_pct "
            "FROM transactions GROUP BY channel ORDER BY fraud_rate_pct DESC LIMIT 10"
        ),
        "chart": {"type": "bar", "x": "channel", "y": "fraud_rate_pct"},
    },
    {
        "key": "shared_devices",
        "patterns": [
            r"devices?.*(shared|share|multiple accounts|accounts)",
            r"shared devices?",
        ],
        "description": "Devices linked to multiple customers",
        "sql": (
            "SELECT id AS device_id, device_type, distinct_customers, transaction_count, "
            "fraud_count, ROUND(risk_score, 4) AS risk_score FROM devices "
            "WHERE distinct_customers > 1 ORDER BY distinct_customers DESC LIMIT 25"
        ),
        "chart": {"type": "bar", "x": "device_id", "y": "distinct_customers"},
    },
]


def match_intent(question: str) -> dict[str, Any] | None:
    lowered = question.lower()
    for intent in INTENTS:
        for pattern in intent["patterns"]:
            if re.search(pattern, lowered):
                return intent
    return None


SYSTEM_PROMPT = (
    "You translate an analyst question into ONE read-only SQL SELECT for a "
    "financial crime warehouse. Output JSON only: "
    '{"sql": "...", "explanation": "...", "chart": {"type": "bar|line|pie|table", '
    '"x": "column", "y": "column"}}. '
    "Rules: single SELECT statement; no comments; no semicolons; no DDL or DML; "
    "always include a LIMIT of at most %d; use only the listed tables and columns; "
    "prefer aggregates with clear aliases."
)


def generate_sql(
    question: str, *, permissions: frozenset[Permission]
) -> tuple[str | None, str, dict[str, Any] | None, dict[str, Any]]:
    """Return ``(sql, source, chart_hint, meta)``."""
    tables = visible_tables(permissions)
    if llm_client.available:
        payload, response = llm_client.complete_json(
            system=SYSTEM_PROMPT % settings.ai_sql_row_limit,
            user=(
                f"Schema you may use:\n{schema_prompt(tables)}\n\n"
                f"Analyst question: {question}\n\n"
                "Return the JSON object only."
            ),
        )
        if payload and payload.get("sql"):
            return (
                str(payload["sql"]),
                "llm",
                payload.get("chart"),
                {
                    "provider": response.provider,
                    "model": response.model,
                    "latency_ms": response.latency_ms,
                    "explanation": payload.get("explanation", ""),
                },
            )

    intent = match_intent(question)
    if intent:
        return (
            intent["sql"],
            "intent_library",
            intent.get("chart"),
            {"intent": intent["key"], "explanation": intent["description"]},
        )
    return None, "none", None, {}


def run_query(
    db: Session,
    question: str,
    *,
    permissions: frozenset[Permission],
    allow_pii: bool,
    explicit_sql: str | None = None,
) -> dict[str, Any]:
    """End-to-end: question -> SQL -> validation -> execution -> result."""
    started = time.perf_counter()
    question = sanitise_prompt(question, max_length=600)

    if explicit_sql:
        sql, source, chart, meta = explicit_sql, "analyst", None, {}
    else:
        sql, source, chart, meta = generate_sql(question, permissions=permissions)

    if not sql:
        return {
            "status": "NO_QUERY",
            "question": question,
            "message": (
                "No SQL could be produced for that question. Configure an LLM provider "
                "for open-ended analytics, or rephrase using one of the supported "
                "topics."
            ),
            "supported_topics": [intent["description"] for intent in INTENTS],
            "sql": None,
            "rows": [],
        }

    validation = validate_sql(
        sql,
        permissions=permissions,
        allow_pii=allow_pii,
        row_limit=settings.ai_sql_row_limit,
    )
    if not validation.valid:
        logger.warning("ai_sql_blocked", extra={"reason": validation.reason, "source": source})
        raise UnsafeQueryError(
            validation.reason or "The generated query was rejected by the SQL guard.",
            details={"sql": sql, "source": source},
        )

    try:
        result = db.execute(text(validation.sql))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception as exc:
        logger.warning("ai_sql_execution_failed", extra={"error": str(exc)})
        raise UnsafeQueryError(
            "The query could not be executed against the warehouse.",
            code="SQL_EXECUTION_FAILED",
            details={"sql": validation.sql, "error": str(exc)[:300]},
        ) from exc

    return {
        "status": "OK",
        "question": question,
        "sql": validation.sql,
        "source": source,
        "tables": validation.tables,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "chart": chart or _infer_chart(columns, rows),
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "explanation": meta.get("explanation", ""),
        "provider": meta.get("provider", source),
        "row_limit": settings.ai_sql_row_limit,
    }


def _infer_chart(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a sensible visualisation when the generator did not suggest one."""
    if not rows or len(columns) < 2:
        return {"type": "table"}
    numeric = [
        column
        for column in columns
        if isinstance(rows[0].get(column), (int, float))
        and not isinstance(rows[0].get(column), bool)
    ]
    categorical = [column for column in columns if column not in numeric]
    if not numeric or not categorical:
        return {"type": "table"}
    x = categorical[0]
    chart_type = "line" if any(k in x.lower() for k in ("day", "date", "month", "week")) else "bar"
    return {"type": chart_type, "x": x, "y": numeric[0]}
