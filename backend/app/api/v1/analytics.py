"""Financial, fraud, customer, merchant analytics and forecasting."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require
from app.core.errors import ValidationError
from app.core.rbac import Permission
from app.services import analytics as analytics_service
from app.services import forecasting as forecast_service

router = APIRouter(tags=["analytics"])

Days = Annotated[int, Query(ge=1, le=365, description="Rolling window in days")]


@router.get("/analytics/overview", summary="Command centre KPIs")
def overview(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
) -> dict[str, Any]:
    return analytics_service.overview(db, days=days)


@router.get("/analytics/timeseries", summary="Volume, fraud and decision time series")
def timeseries(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
    bucket: Annotated[str, Query(pattern="^(hour|day|week)$")] = "day",
) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "days": days,
        "points": analytics_service.timeseries(db, days=days, bucket=bucket),
    }


@router.get("/analytics/breakdown/{dimension}", summary="Aggregate by dimension")
def breakdown(
    dimension: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> dict[str, Any]:
    try:
        rows = analytics_service.breakdown(db, dimension, days=days, limit=limit)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return {"dimension": dimension, "days": days, "items": rows}


@router.get("/analytics/losses", summary="Fraud loss analytics")
def losses(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
) -> dict[str, Any]:
    return analytics_service.loss_analytics(db, days=days)


@router.get("/analytics/performance", summary="Detection performance of the decision engine")
def performance(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
) -> dict[str, Any]:
    return analytics_service.detection_performance(db, days=days)


@router.get("/analytics/merchants", summary="Merchant risk analytics")
def merchants(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return {"items": analytics_service.merchant_analytics(db, limit=limit, days=days)}


@router.get("/analytics/customers", summary="Customer risk and value analytics")
def customers(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return analytics_service.customer_analytics(db, limit=limit)


@router.get("/analytics/heatmap", summary="Fraud heatmap by hour, weekday and category")
def heatmap(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
) -> dict[str, Any]:
    return analytics_service.fraud_heatmap(db, days=days)


@router.get("/analytics/geography", summary="Per-city rollup for the risk map")
def geography(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))],
    days: Days = 30,
) -> dict[str, Any]:
    return {"days": days, "locations": analytics_service.geography(db, days=days)}


@router.get("/analytics/operations", summary="Alert and case queue health")
def operations(
    db: DbSession, user: Annotated[Any, Depends(require(Permission.ANALYTICS_READ))]
) -> dict[str, Any]:
    return analytics_service.operations_snapshot(db)


@router.get("/forecasting/{metric}", summary="Forecast a metric with confidence intervals")
def forecast(
    metric: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.FORECAST_READ))],
    horizons: Annotated[str, Query(description="Comma separated horizons in days")] = "7,30,90",
) -> dict[str, Any]:
    try:
        parsed = tuple(sorted({int(h) for h in horizons.split(",") if h.strip()}))
        if not parsed or any(h < 1 or h > 180 for h in parsed):
            raise ValueError("horizons must be between 1 and 180 days")
        return forecast_service.forecast(db, metric=metric, horizons=parsed)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@router.get("/forecasting-workload", summary="Investigation workload forecast")
def workload(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.FORECAST_READ))],
    horizon: Annotated[int, Query(ge=1, le=90)] = 7,
) -> dict[str, Any]:
    return forecast_service.workload_forecast(db, horizon=horizon)
