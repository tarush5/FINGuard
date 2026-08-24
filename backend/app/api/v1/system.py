"""Health, observability, notifications and dead-letter operations."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUserDep, DbSession, PaginationDep, require
from app.core.cache import cache
from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.rbac import Permission
from app.db.models.core import DeadLetterEvent, Transaction
from app.db.models.identity import Notification
from app.db.models.platform import PipelineRun
from app.db.session import database_stats, ping
from app.events.bus import event_bus
from app.events.schemas import EventEnvelope
from app.services import audit
from app.services.monitoring import LATENCY_TARGETS, metrics
from app.utils import safe_float, utcnow

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "mode": settings.platform_mode,
        "time": utcnow().isoformat(),
    }


@router.get("/ready", summary="Readiness probe: database, cache and event bus")
def ready() -> dict[str, Any]:
    database_ok = ping()
    cache_ok = cache.healthy()
    bus_ok = event_bus.healthy()
    ready_state = database_ok and cache_ok
    return {
        "status": "ready" if ready_state else "degraded",
        "checks": {
            "database": {"ok": database_ok, "dialect": database_stats()["dialect"]},
            "cache": {"ok": cache_ok, "backend": cache.name},
            "event_bus": {"ok": bus_ok, "driver": event_bus.driver},
        },
    }


@router.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
def prometheus_metrics() -> str:
    return metrics.prometheus()


@router.get("/monitoring/system", summary="System health dashboard payload")
def system_health(
    db: DbSession, user: Annotated[Any, Depends(require(Permission.MONITORING_READ))]
) -> dict[str, Any]:
    snapshot = metrics.snapshot()
    bus_stats = event_bus.stats()

    latencies = []
    for name, target in LATENCY_TARGETS.items():
        measured = snapshot["latencies"].get(name)
        if not measured:
            continue
        latencies.append(
            {
                "name": name,
                "target_p95_ms": target,
                "p50": measured["p50"],
                "p95": measured["p95"],
                "p99": measured["p99"],
                "avg": measured["avg"],
                "samples": measured["window"],
                "within_target": measured["p95"] <= target if measured["window"] else None,
            }
        )

    dlq_count = int(
        db.execute(
            select(func.count())
            .select_from(DeadLetterEvent)
            .where(DeadLetterEvent.status == "FAILED")
        ).scalar_one()
        or 0
    )
    recent_runs = list(
        db.execute(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(10)).scalars()
    )
    failed_runs = int(
        db.execute(
            select(func.count()).select_from(PipelineRun).where(PipelineRun.status == "FAILED")
        ).scalar_one()
        or 0
    )
    throughput_window = utcnow() - timedelta(hours=1)
    recent_transactions = int(
        db.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.created_at >= throughput_window)
        ).scalar_one()
        or 0
    )

    components = [
        {
            "component": "api",
            "status": "HEALTHY",
            "detail": f"{snapshot['counters'].get('api.requests', 0):.0f} requests since start",
        },
        {
            "component": "database",
            "status": "HEALTHY" if ping() else "CRITICAL",
            "detail": f"{database_stats()['queries']} queries, avg {database_stats()['avg_query_ms']} ms",
        },
        {
            "component": "cache",
            "status": "HEALTHY" if cache.healthy() else "WARNING",
            "detail": f"backend={cache.name}",
        },
        {
            "component": "event_bus",
            "status": "HEALTHY" if event_bus.healthy() else "WARNING",
            "detail": f"driver={event_bus.driver}",
        },
        {
            "component": "dead_letter_queue",
            "status": "HEALTHY" if dlq_count == 0 else "WARNING",
            "detail": f"{dlq_count} unresolved event(s)",
        },
        {
            "component": "pipelines",
            "status": "HEALTHY" if failed_runs == 0 else "WARNING",
            "detail": f"{failed_runs} failed run(s)",
        },
    ]

    return {
        "status": (
            "CRITICAL"
            if any(c["status"] == "CRITICAL" for c in components)
            else "WARNING" if any(c["status"] == "WARNING" for c in components) else "HEALTHY"
        ),
        "uptime_seconds": snapshot["uptime_seconds"],
        "components": components,
        "latency": latencies,
        "counters": snapshot["counters"],
        "gauges": snapshot["gauges"],
        "event_bus": bus_stats,
        "database": database_stats(),
        "throughput": {
            "transactions_last_hour": recent_transactions,
            "decisions_total": snapshot["counters"].get("pipeline.processed", 0),
            "duplicates_rejected": snapshot["counters"].get("pipeline.duplicates", 0),
        },
        "pipelines": [
            {
                "id": run.id,
                "pipeline": run.pipeline,
                "type": run.pipeline_type,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "duration_ms": safe_float(run.duration_ms),
                "records_in": run.records_in,
                "records_out": run.records_out,
            }
            for run in recent_runs
        ],
    }


@router.get("/monitoring/latency", summary="Latency percentiles against targets")
def latency(user: Annotated[Any, Depends(require(Permission.MONITORING_READ))]) -> dict[str, Any]:
    snapshot = metrics.snapshot()["latencies"]
    return {
        "targets": LATENCY_TARGETS,
        "measured": snapshot,
        "note": (
            "Percentiles are computed over the most recent samples retained in "
            "memory (up to 2000 per metric), not over all time."
        ),
    }


@router.get("/notifications", summary="Notification feed")
def notifications(
    db: DbSession,
    user: CurrentUserDep,
    page: PaginationDep,
    unread_only: Annotated[bool, Query()] = False,
    severity: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Notification)
    count_stmt = select(func.count()).select_from(Notification)
    conditions = []
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    if severity:
        conditions.append(Notification.severity == severity.upper())
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)
    unread = int(
        db.execute(
            select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
        ).scalar_one()
        or 0
    )
    rows = db.execute(
        stmt.order_by(Notification.created_at.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    envelope = page.envelope(
        [
            {
                "id": n.id,
                "severity": n.severity,
                "category": n.category,
                "title": n.title,
                "body": n.body,
                "entity_type": n.entity_type,
                "entity_id": n.entity_id,
                "link": n.link,
                "target_role": n.target_role,
                "read": n.read_at is not None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        total,
    )
    envelope["unread_count"] = unread
    return envelope


@router.post("/notifications/{notification_id}/read", summary="Mark a notification read")
def mark_read(notification_id: str, db: DbSession, user: CurrentUserDep) -> dict[str, Any]:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotFoundError(f"Notification {notification_id} was not found.")
    notification.read_at = utcnow()
    db.commit()
    return {"id": notification.id, "read": True}


@router.post("/notifications/read-all", summary="Mark every notification read")
def mark_all_read(db: DbSession, user: CurrentUserDep) -> dict[str, Any]:
    rows = db.execute(select(Notification).where(Notification.read_at.is_(None))).scalars()
    count = 0
    for notification in rows:
        notification.read_at = utcnow()
        count += 1
    db.commit()
    return {"marked_read": count}


@router.get("/events/dead-letter", summary="Events that exhausted their retries")
def dead_letter(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MONITORING_READ))],
    page: PaginationDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    stmt = select(DeadLetterEvent)
    count_stmt = select(func.count()).select_from(DeadLetterEvent)
    if status_filter:
        stmt = stmt.where(DeadLetterEvent.status == status_filter.upper())
        count_stmt = count_stmt.where(DeadLetterEvent.status == status_filter.upper())
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(DeadLetterEvent.created_at.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    return page.envelope(
        [
            {
                "id": row.id,
                "event_id": row.event_id,
                "topic": row.topic,
                "attempts": row.attempts,
                "error_type": row.error_type,
                "error_message": row.error_message,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "replayed_at": row.replayed_at.isoformat() if row.replayed_at else None,
                "payload": row.payload,
            }
            for row in rows
        ],
        total,
    )


@router.post("/events/dead-letter/{record_id}/replay", summary="Replay a dead-lettered event")
def replay(
    record_id: str,
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.PIPELINE_RUN))],
) -> dict[str, Any]:
    record = db.get(DeadLetterEvent, record_id)
    if record is None:
        raise NotFoundError(f"Dead letter record {record_id} was not found.")
    if record.status == "REPLAYED":
        return {"id": record.id, "status": record.status, "message": "Already replayed."}

    envelope = EventEnvelope.model_validate(record.payload)
    envelope.attempt += 1
    envelope.topic = envelope.topic.replace(".dlq", "")
    event_bus.publish(envelope)

    record.status = "REPLAYED"
    record.replayed_at = utcnow()
    audit.record(
        db,
        action="event.replayed",
        entity_type="EVENT",
        entity_id=record.event_id,
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"topic": envelope.topic},
    )
    db.commit()
    return {"id": record.id, "status": record.status, "topic": envelope.topic}


@router.get("/events/topics", summary="Kafka topic statistics")
def topics(user: Annotated[Any, Depends(require(Permission.MONITORING_READ))]) -> dict[str, Any]:
    stats = event_bus.stats()
    return {
        "driver": stats["driver"],
        "running": stats.get("running", False),
        "brokers": stats.get("brokers"),
        "topics": [{"topic": topic, **values} for topic, values in stats.get("topics", {}).items()],
    }
