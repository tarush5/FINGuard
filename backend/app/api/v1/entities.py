"""Customer, merchant and device endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, PaginationDep, SortingDep, require
from app.core.errors import NotFoundError
from app.core.rbac import Permission
from app.db.models.core import (
    Account,
    Customer,
    Device,
    DeviceLink,
    Merchant,
    Transaction,
)
from app.db.models.risk import Case
from app.services.serializers import (
    serialize_account,
    serialize_customer,
    serialize_device,
    serialize_merchant,
    serialize_transaction,
)
from app.utils import safe_float

router = APIRouter(tags=["entities"])


# ------------------------------------------------------------------ customers


@router.get("/customers", summary="Search customers")
def list_customers(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CUSTOMER_READ))],
    page: PaginationDep,
    sort: SortingDep,
    search: Annotated[str | None, Query(max_length=80)] = None,
    risk_band: Annotated[str | None, Query()] = None,
    segment: Annotated[str | None, Query()] = None,
    country: Annotated[str | None, Query(max_length=2)] = None,
    watchlisted: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Customer).where(Customer.is_deleted.is_(False))
    count_stmt = select(func.count()).select_from(Customer).where(Customer.is_deleted.is_(False))
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(or_(Customer.id.ilike(like), Customer.full_name.ilike(like)))
    if risk_band:
        conditions.append(Customer.risk_band == risk_band.upper())
    if segment:
        conditions.append(Customer.segment == segment.upper())
    if country:
        conditions.append(Customer.country == country.upper())
    if watchlisted is not None:
        conditions.append(Customer.watchlisted.is_(watchlisted))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = sort.apply(
        stmt,
        {
            "risk_score": Customer.risk_score,
            "lifetime_value": Customer.lifetime_value,
            "transaction_count": Customer.transaction_count,
            "onboarded_at": Customer.onboarded_at,
        },
        Customer.risk_score,
    )
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.offset(page.offset).limit(page.limit)).scalars()
    return page.envelope([serialize_customer(c, mask_pii=user.mask_pii) for c in rows], total)


@router.get("/customers/{customer_id}", summary="Customer 360")
def get_customer(
    customer_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.CUSTOMER_READ))],
) -> dict[str, Any]:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"Customer {customer_id} was not found.", code="CUSTOMER_NOT_FOUND")

    accounts = list(db.execute(select(Account).where(Account.customer_id == customer_id)).scalars())
    transactions = list(
        db.execute(
            select(Transaction)
            .where(Transaction.customer_id == customer_id)
            .order_by(Transaction.occurred_at.desc())
            .limit(25)
        ).scalars()
    )
    device_rows = list(
        db.execute(
            select(Device, DeviceLink)
            .join(DeviceLink, DeviceLink.device_id == Device.id)
            .where(DeviceLink.customer_id == customer_id)
            .order_by(DeviceLink.last_seen_at.desc())
        )
    )
    cases = list(
        db.execute(
            select(Case)
            .where(Case.customer_id == customer_id)
            .order_by(Case.created_at.desc())
            .limit(10)
        ).scalars()
    )
    stats = db.execute(
        select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            func.coalesce(func.avg(Transaction.risk_score), 0.0),
            func.coalesce(func.max(Transaction.risk_score), 0.0),
        ).where(Transaction.customer_id == customer_id)
    ).one()

    return {
        "customer": serialize_customer(customer, mask_pii=user.mask_pii),
        "accounts": [serialize_account(a) for a in accounts],
        "recent_transactions": [
            serialize_transaction(t, mask_pii=user.mask_pii) for t in transactions
        ],
        "devices": [
            {
                **(serialize_device(device) or {}),
                "first_seen_for_customer": (
                    link.first_seen_at.isoformat() if link.first_seen_at else None
                ),
                "last_seen_for_customer": (
                    link.last_seen_at.isoformat() if link.last_seen_at else None
                ),
                "transactions_on_device": link.transaction_count,
                "shared_with_others": max((device.distinct_customers or 1) - 1, 0),
            }
            for device, link in device_rows
        ],
        "cases": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "status": case.status,
                "risk_band": case.risk_band,
                "risk_score": safe_float(case.risk_score),
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
            for case in cases
        ],
        "statistics": {
            "transactions": int(stats[0] or 0),
            "total_volume": round(float(stats[1] or 0), 2),
            "average_risk": round(float(stats[2] or 0), 2),
            "peak_risk": round(float(stats[3] or 0), 2),
        },
    }


# ------------------------------------------------------------------ merchants


@router.get("/merchants", summary="Search merchants")
def list_merchants(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MERCHANT_READ))],
    page: PaginationDep,
    sort: SortingDep,
    search: Annotated[str | None, Query(max_length=80)] = None,
    category: Annotated[str | None, Query()] = None,
    risk_band: Annotated[str | None, Query()] = None,
    high_risk_only: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    stmt = select(Merchant).where(Merchant.is_deleted.is_(False))
    count_stmt = select(func.count()).select_from(Merchant).where(Merchant.is_deleted.is_(False))
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(or_(Merchant.id.ilike(like), Merchant.name.ilike(like)))
    if category:
        conditions.append(Merchant.category == category.upper())
    if risk_band:
        conditions.append(Merchant.risk_band == risk_band.upper())
    if high_risk_only:
        conditions.append(Merchant.high_risk_flag.is_(True))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = sort.apply(
        stmt,
        {
            "risk_score": Merchant.risk_score,
            "fraud_rate": Merchant.fraud_rate,
            "transaction_volume": Merchant.transaction_volume,
            "transaction_count": Merchant.transaction_count,
        },
        Merchant.risk_score,
    )
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.offset(page.offset).limit(page.limit)).scalars()
    return page.envelope([serialize_merchant(m) for m in rows], total)


@router.get("/merchants/{merchant_id}", summary="Merchant detail with risk profile")
def get_merchant(
    merchant_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.MERCHANT_READ))],
) -> dict[str, Any]:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise NotFoundError(f"Merchant {merchant_id} was not found.", code="MERCHANT_NOT_FOUND")

    transactions = list(
        db.execute(
            select(Transaction)
            .where(Transaction.merchant_id == merchant_id)
            .order_by(Transaction.occurred_at.desc())
            .limit(25)
        ).scalars()
    )
    decisions = db.execute(
        select(Transaction.decision, func.count())
        .where(Transaction.merchant_id == merchant_id)
        .group_by(Transaction.decision)
    ).all()
    top_customers = db.execute(
        select(
            Transaction.customer_id,
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
        )
        .where(Transaction.merchant_id == merchant_id)
        .group_by(Transaction.customer_id)
        .order_by(func.coalesce(func.sum(Transaction.amount), 0.0).desc())
        .limit(10)
    ).all()

    return {
        "merchant": serialize_merchant(merchant),
        "recent_transactions": [
            serialize_transaction(t, mask_pii=user.mask_pii) for t in transactions
        ],
        "decision_mix": [{"decision": d, "count": int(c)} for d, c in decisions],
        "top_customers": [
            {"customer_id": cid, "transactions": int(count), "volume": round(float(volume or 0), 2)}
            for cid, count, volume in top_customers
        ],
    }


# -------------------------------------------------------------------- devices


@router.get("/devices", summary="Search devices")
def list_devices(
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_READ))],
    page: PaginationDep,
    sort: SortingDep,
    shared_only: Annotated[bool, Query(description="Only devices linked to 2+ customers")] = False,
    blacklisted: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    stmt = select(Device)
    count_stmt = select(func.count()).select_from(Device)
    conditions = []
    if shared_only:
        conditions.append(Device.distinct_customers > 1)
    if blacklisted is not None:
        conditions.append(Device.is_blacklisted.is_(blacklisted))
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = sort.apply(
        stmt,
        {
            "risk_score": Device.risk_score,
            "distinct_customers": Device.distinct_customers,
            "transaction_count": Device.transaction_count,
            "last_seen_at": Device.last_seen_at,
        },
        Device.risk_score,
    )
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.offset(page.offset).limit(page.limit)).scalars()
    return page.envelope([serialize_device(d) for d in rows], total)


@router.get("/devices/{device_id}", summary="Device detail and linked accounts")
def get_device(
    device_id: str,
    db: DbSession,
    user: Annotated[Any, Depends(require(Permission.TRANSACTION_READ))],
) -> dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise NotFoundError(f"Device {device_id} was not found.", code="DEVICE_NOT_FOUND")
    links = list(db.execute(select(DeviceLink).where(DeviceLink.device_id == device_id)).scalars())
    transactions = list(
        db.execute(
            select(Transaction)
            .where(Transaction.device_id == device_id)
            .order_by(Transaction.occurred_at.desc())
            .limit(25)
        ).scalars()
    )
    return {
        "device": serialize_device(device),
        "linked_customers": [
            {
                "customer_id": link.customer_id,
                "first_seen_at": link.first_seen_at.isoformat() if link.first_seen_at else None,
                "last_seen_at": link.last_seen_at.isoformat() if link.last_seen_at else None,
                "transactions": link.transaction_count,
                "total_amount": safe_float(link.total_amount),
                "fraud_count": link.fraud_count,
            }
            for link in links
        ],
        "recent_transactions": [
            serialize_transaction(t, mask_pii=user.mask_pii) for t in transactions
        ],
    }
