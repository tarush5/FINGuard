"""Data platform: catalogue, pipelines, quality and lineage."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app.api.deps import DbSession, PaginationDep, require
from app.core.errors import NotFoundError
from app.core.rbac import Permission
from app.db.models.platform import Dataset, LineageEdge, PipelineRun, QualityCheck
from app.services import audit
from app.services import quality as quality_service
from app.utils import safe_float

router = APIRouter(tags=["platform"])


@router.get("/datasets", summary="Data catalogue")
def datasets(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.DATA_READ))],
    layer: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Dataset)
    if layer:
        stmt = stmt.where(Dataset.layer == layer)
    rows = list(db.execute(stmt.order_by(Dataset.layer, Dataset.name)).scalars())
    return {
        "items": [
            {
                "id": ds.id,
                "name": ds.name,
                "layer": ds.layer,
                "source_system": ds.source_system,
                "description": ds.description,
                "owner": ds.owner,
                "steward": ds.steward,
                "classification": ds.classification,
                "contains_pii": ds.contains_pii,
                "row_count": ds.row_count,
                "column_count": ds.column_count,
                "size_bytes": ds.size_bytes,
                "refresh_cadence": ds.refresh_cadence,
                "last_refreshed_at": (
                    ds.last_refreshed_at.isoformat() if ds.last_refreshed_at else None
                ),
                "freshness_sla_minutes": ds.freshness_sla_minutes,
                "quality_score": safe_float(ds.quality_score),
                "tags": ds.tags or [],
            }
            for ds in rows
        ],
        "layers": sorted({ds.layer for ds in rows}),
    }


@router.get("/datasets/{name}", summary="Dataset detail with its quality history")
def dataset_detail(
    name: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.DATA_READ))],
) -> dict[str, Any]:
    dataset = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
    if dataset is None:
        raise NotFoundError(f"Dataset {name} was not found.", code="DATASET_NOT_FOUND")
    checks = list(
        db.execute(
            select(QualityCheck)
            .where(QualityCheck.dataset == name)
            .order_by(QualityCheck.run_at.desc())
            .limit(40)
        ).scalars()
    )
    upstream = list(db.execute(select(LineageEdge).where(LineageEdge.target == name)).scalars())
    downstream = list(db.execute(select(LineageEdge).where(LineageEdge.source == name)).scalars())
    return {
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "layer": dataset.layer,
            "description": dataset.description,
            "owner": dataset.owner,
            "classification": dataset.classification,
            "contains_pii": dataset.contains_pii,
            "row_count": dataset.row_count,
            "quality_score": safe_float(dataset.quality_score),
            "last_refreshed_at": (
                dataset.last_refreshed_at.isoformat() if dataset.last_refreshed_at else None
            ),
        },
        "checks": [
            {
                "check_name": check.check_name,
                "dimension": check.dimension,
                "status": check.status,
                "score": safe_float(check.score),
                "rows_scanned": check.rows_scanned,
                "rows_failed": check.rows_failed,
                "run_at": check.run_at.isoformat() if check.run_at else None,
            }
            for check in checks
        ],
        "upstream": [edge.source for edge in upstream],
        "downstream": [edge.target for edge in downstream],
    }


@router.get("/pipelines", summary="Pipeline run history")
def pipelines(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.DATA_READ))],
    page: PaginationDep,
    pipeline: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    stmt = select(PipelineRun)
    count_stmt = select(func.count()).select_from(PipelineRun)
    if pipeline:
        stmt = stmt.where(PipelineRun.pipeline == pipeline)
        count_stmt = count_stmt.where(PipelineRun.pipeline == pipeline)
    if status_filter:
        stmt = stmt.where(PipelineRun.status == status_filter.upper())
        count_stmt = count_stmt.where(PipelineRun.status == status_filter.upper())

    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(
        stmt.order_by(PipelineRun.started_at.desc()).offset(page.offset).limit(page.limit)
    ).scalars()
    envelope = page.envelope(
        [
            {
                "id": run.id,
                "pipeline": run.pipeline,
                "pipeline_type": run.pipeline_type,
                "run_key": run.run_key,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_ms": safe_float(run.duration_ms),
                "records_in": run.records_in,
                "records_out": run.records_out,
                "records_failed": run.records_failed,
                "triggered_by": run.triggered_by,
                "metrics": run.metrics or {},
                "steps": run.steps or [],
                "error": run.error,
            }
            for run in rows
        ],
        total,
    )
    summary = db.execute(
        select(
            PipelineRun.pipeline,
            func.count(),
            func.coalesce(func.avg(PipelineRun.duration_ms), 0.0),
            func.sum(func.coalesce(PipelineRun.records_out, 0)),
        ).group_by(PipelineRun.pipeline)
    ).all()
    envelope["summary"] = [
        {
            "pipeline": name,
            "runs": int(count),
            "average_duration_ms": round(float(duration or 0), 2),
            "records_processed": int(records or 0),
        }
        for name, count, duration, records in summary
    ]
    return envelope


@router.get("/quality", summary="Financial data trust score and check results")
def quality(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.DATA_READ))],
    recompute: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    if recompute:
        result = quality_service.run_checks(db)
        db.commit()
    else:
        result = quality_service.latest_summary(db)
    result["trend"] = quality_service.trend(db)
    return result


@router.post("/quality/run", summary="Run the data quality suite now")
def run_quality(
    request: Request,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.PIPELINE_RUN))],
) -> dict[str, Any]:
    result = quality_service.run_checks(db)
    audit.record(
        db,
        action="quality.suite_run",
        entity_type="DATASET",
        actor_id=user.id,
        actor_email=user.email,
        actor_roles=user.roles,
        request=request,
        details={"trust_score": result["trust_score"], "failed": len(result["failed_checks"])},
    )
    db.commit()
    return result


@router.get("/lineage", summary="Interactive data lineage graph")
def lineage(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.DATA_READ))],
) -> dict[str, Any]:
    edges = list(db.execute(select(LineageEdge)).scalars())
    datasets_by_name = {ds.name: ds for ds in db.execute(select(Dataset)).scalars()}
    node_names = {edge.source for edge in edges} | {edge.target for edge in edges}
    nodes = []
    for name in sorted(node_names):
        dataset = datasets_by_name.get(name)
        nodes.append(
            {
                "id": name,
                "label": name,
                "type": "dataset" if dataset else "process",
                "layer": dataset.layer if dataset else "processing",
                "row_count": dataset.row_count if dataset else None,
                "quality_score": safe_float(dataset.quality_score) if dataset else None,
                "contains_pii": dataset.contains_pii if dataset else False,
            }
        )
    return {
        "nodes": nodes,
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "transformation": edge.transformation,
                "processor": edge.processor,
            }
            for edge in edges
        ],
    }
