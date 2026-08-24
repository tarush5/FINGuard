"""Data quality engine.

Checks run as real SQL against the warehouse tables -- nothing here is a static
number.  Each check reports rows scanned, rows failed and a score; the checks
roll up into per-dimension scores and a single Financial Data Trust Score.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Merchant, Transaction, TransactionFeature
from app.db.models.platform import Dataset, QualityCheck
from app.utils import safe_float

logger = get_logger(__name__)

DIMENSIONS = ("COMPLETENESS", "VALIDITY", "CONSISTENCY", "UNIQUENESS", "FRESHNESS", "ACCURACY")
VALID_CURRENCIES = {"INR", "USD", "EUR", "GBP", "SGD", "AED", "AUD"}
FRESHNESS_SLA_MINUTES = 15


@dataclass
class CheckResult:
    dataset: str
    check_name: str
    dimension: str
    expectation: str
    rows_scanned: int
    rows_failed: int
    threshold: float
    duration_ms: float
    details: dict[str, Any]

    @property
    def score(self) -> float:
        if self.rows_scanned == 0:
            return 100.0
        return round((1 - self.rows_failed / self.rows_scanned) * 100, 3)

    @property
    def status(self) -> str:
        if self.score >= self.threshold:
            return "PASS"
        return "WARN" if self.score >= self.threshold - 2.0 else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "check_name": self.check_name,
            "dimension": self.dimension,
            "expectation": self.expectation,
            "rows_scanned": self.rows_scanned,
            "rows_failed": self.rows_failed,
            "score": self.score,
            "status": self.status,
            "threshold": self.threshold,
            "duration_ms": round(self.duration_ms, 2),
            "details": self.details,
        }


def _count(db: Session, stmt: Any) -> int:
    return int(db.execute(stmt).scalar_one() or 0)


def _timed(fn: Callable[[], CheckResult]) -> CheckResult:
    started = time.perf_counter()
    result = fn()
    result.duration_ms = (time.perf_counter() - started) * 1000
    return result


def run_checks(db: Session, *, persist: bool = True) -> dict[str, Any]:
    """Execute every quality check and roll the results up into a trust score."""
    total_txns = _count(db, select(func.count()).select_from(Transaction))
    results: list[CheckResult] = []

    # ---------------------------------------------------------- completeness
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="customer_id_not_null",
                dimension="COMPLETENESS",
                expectation="every transaction references a customer",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(or_(Transaction.customer_id.is_(None), Transaction.customer_id == "")),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="device_id_present",
                dimension="COMPLETENESS",
                expectation="device fingerprint captured for >= 97% of transactions",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(or_(Transaction.device_id.is_(None), Transaction.device_id == "")),
                ),
                threshold=97.0,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="geolocation_present",
                dimension="COMPLETENESS",
                expectation="coordinates present for >= 95% of transactions",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(or_(Transaction.latitude.is_(None), Transaction.longitude.is_(None))),
                ),
                threshold=95.0,
                duration_ms=0.0,
                details={},
            )
        )
    )

    # -------------------------------------------------------------- validity
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="amount_positive",
                dimension="VALIDITY",
                expectation="amount > 0 and below the 1e12 ceiling",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(or_(Transaction.amount <= 0, Transaction.amount > 1e12)),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="currency_iso4217",
                dimension="VALIDITY",
                expectation=f"currency in {sorted(VALID_CURRENCIES)}",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.currency.notin_(list(VALID_CURRENCIES))),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="coordinates_in_range",
                dimension="VALIDITY",
                expectation="-90<=lat<=90 and -180<=lon<=180",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(
                        or_(
                            Transaction.latitude < -90,
                            Transaction.latitude > 90,
                            Transaction.longitude < -180,
                            Transaction.longitude > 180,
                        )
                    ),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="timestamp_not_future",
                dimension="VALIDITY",
                expectation="occurred_at is not in the future",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.occurred_at > utcnow() + timedelta(minutes=5)),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )

    # ------------------------------------------------------------ uniqueness
    def duplicate_events() -> CheckResult:
        duplicate_groups = db.execute(
            select(func.count()).select_from(
                select(Transaction.event_id)
                .group_by(Transaction.event_id)
                .having(func.count(Transaction.id) > 1)
                .subquery()
            )
        ).scalar_one()
        return CheckResult(
            dataset="transactions_enriched",
            check_name="event_id_unique",
            dimension="UNIQUENESS",
            expectation="event_id appears exactly once (idempotent ingestion)",
            rows_scanned=total_txns,
            rows_failed=int(duplicate_groups or 0),
            threshold=100.0,
            duration_ms=0.0,
            details={"duplicate_event_groups": int(duplicate_groups or 0)},
        )

    results.append(_timed(duplicate_events))

    # ----------------------------------------------------------- consistency
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transaction_features",
                check_name="features_exist_for_transactions",
                dimension="CONSISTENCY",
                expectation="every scored transaction has a stored feature vector",
                rows_scanned=total_txns,
                rows_failed=max(
                    total_txns - _count(db, select(func.count()).select_from(TransactionFeature)),
                    0,
                ),
                threshold=99.5,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="transactions_enriched",
                check_name="decision_assigned",
                dimension="CONSISTENCY",
                expectation="no transaction is left in PENDING after scoring",
                rows_scanned=total_txns,
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Transaction)
                    .where(Transaction.decision == "PENDING"),
                ),
                threshold=99.9,
                duration_ms=0.0,
                details={},
            )
        )
    )
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="merchants",
                check_name="fraud_rate_bounded",
                dimension="CONSISTENCY",
                expectation="0 <= fraud_rate <= 1",
                rows_scanned=_count(db, select(func.count()).select_from(Merchant)),
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Merchant)
                    .where(or_(Merchant.fraud_rate < 0, Merchant.fraud_rate > 1)),
                ),
                threshold=100.0,
                duration_ms=0.0,
                details={},
            )
        )
    )

    # ------------------------------------------------------------- freshness
    def freshness() -> CheckResult:
        latest = db.execute(select(func.max(Transaction.occurred_at))).scalar_one_or_none()
        minutes = (
            (utcnow() - latest.replace(tzinfo=latest.tzinfo or utcnow().tzinfo)).total_seconds()
            / 60
            if latest
            else 1e6
        )
        stale = minutes > FRESHNESS_SLA_MINUTES
        return CheckResult(
            dataset="transactions_enriched",
            check_name="ingestion_freshness",
            dimension="FRESHNESS",
            expectation=f"latest transaction within {FRESHNESS_SLA_MINUTES} minutes",
            rows_scanned=1,
            rows_failed=1 if stale else 0,
            threshold=100.0,
            duration_ms=0.0,
            details={"minutes_behind": round(minutes, 2)},
        )

    results.append(_timed(freshness))

    # -------------------------------------------------------------- accuracy
    results.append(
        _timed(
            lambda: CheckResult(
                dataset="customers",
                check_name="profile_matches_history",
                dimension="ACCURACY",
                expectation="customer transaction_count > 0 where transactions exist",
                rows_scanned=_count(db, select(func.count()).select_from(Customer)),
                rows_failed=_count(
                    db,
                    select(func.count())
                    .select_from(Customer)
                    .where(
                        and_(
                            Customer.transaction_count == 0,
                            Customer.id.in_(select(Transaction.customer_id).distinct()),
                        )
                    ),
                ),
                threshold=99.0,
                duration_ms=0.0,
                details={},
            )
        )
    )

    if persist:
        now = utcnow()
        for result in results:
            db.add(
                QualityCheck(
                    id=new_id("QC"),
                    dataset=result.dataset,
                    check_name=result.check_name,
                    dimension=result.dimension,
                    expectation=result.expectation,
                    run_at=now,
                    status=result.status,
                    score=result.score,
                    rows_scanned=result.rows_scanned,
                    rows_failed=result.rows_failed,
                    threshold=result.threshold,
                    duration_ms=result.duration_ms,
                    details=result.details,
                )
            )
        _update_dataset_quality(db, results)

    return summarise(results)


def summarise(results: list[CheckResult]) -> dict[str, Any]:
    by_dimension: dict[str, list[CheckResult]] = {}
    for result in results:
        by_dimension.setdefault(result.dimension, []).append(result)

    dimension_scores = {
        dimension: round(sum(r.score for r in items) / len(items), 2)
        for dimension, items in by_dimension.items()
    }
    trust_score = (
        round(sum(dimension_scores.values()) / len(dimension_scores), 2)
        if dimension_scores
        else 100.0
    )
    return {
        "trust_score": trust_score,
        "status": (
            "HEALTHY" if trust_score >= 98 else "WARNING" if trust_score >= 94 else "CRITICAL"
        ),
        "dimensions": dimension_scores,
        "checks": [result.to_dict() for result in results],
        "failed_checks": [r.to_dict() for r in results if r.status != "PASS"],
        "evaluated_at": utcnow().isoformat(),
    }


def _update_dataset_quality(db: Session, results: list[CheckResult]) -> None:
    by_dataset: dict[str, list[CheckResult]] = {}
    for result in results:
        by_dataset.setdefault(result.dataset, []).append(result)
    for name, items in by_dataset.items():
        dataset = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if dataset is None:
            continue
        dataset.quality_score = round(sum(r.score for r in items) / len(items), 2)
        dataset.last_refreshed_at = utcnow()


def latest_summary(db: Session) -> dict[str, Any]:
    """Most recent run of each check, rebuilt from the persisted results."""
    latest_run = db.execute(select(func.max(QualityCheck.run_at))).scalar_one_or_none()
    if latest_run is None:
        return {"trust_score": 0.0, "status": "UNKNOWN", "dimensions": {}, "checks": []}
    rows = list(db.execute(select(QualityCheck).where(QualityCheck.run_at == latest_run)).scalars())
    dimension_scores: dict[str, list[float]] = {}
    checks = []
    for row in rows:
        dimension_scores.setdefault(row.dimension, []).append(safe_float(row.score))
        checks.append(
            {
                "dataset": row.dataset,
                "check_name": row.check_name,
                "dimension": row.dimension,
                "expectation": row.expectation,
                "rows_scanned": row.rows_scanned,
                "rows_failed": row.rows_failed,
                "score": safe_float(row.score),
                "status": row.status,
                "threshold": safe_float(row.threshold),
                "duration_ms": safe_float(row.duration_ms),
                "details": row.details,
                "run_at": row.run_at.isoformat() if row.run_at else None,
            }
        )
    dimensions = {
        dimension: round(sum(values) / len(values), 2)
        for dimension, values in dimension_scores.items()
    }
    trust = round(sum(dimensions.values()) / len(dimensions), 2) if dimensions else 0.0
    return {
        "trust_score": trust,
        "status": "HEALTHY" if trust >= 98 else "WARNING" if trust >= 94 else "CRITICAL",
        "dimensions": dimensions,
        "checks": checks,
        "failed_checks": [c for c in checks if c["status"] != "PASS"],
        "evaluated_at": latest_run.isoformat() if latest_run else None,
    }


def trend(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            QualityCheck.run_at,
            func.avg(QualityCheck.score),
            func.sum(cast(QualityCheck.status != "PASS", Integer)),
        )
        .group_by(QualityCheck.run_at)
        .order_by(QualityCheck.run_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "run_at": run_at.isoformat() if run_at else None,
            "trust_score": round(float(score or 0), 2),
            "failed_checks": int(failed or 0),
        }
        for run_at, score, failed in reversed(rows)
    ]
