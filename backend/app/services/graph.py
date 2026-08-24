"""Graph intelligence.

Two distinct workloads, deliberately implemented differently:

* **Online graph risk** (:func:`graph_risk`) runs inside the decision path, so it
  must be O(few indexed queries) -- it looks only at the immediate neighbourhood
  of the transaction (device fan-out, IP fan-out, contaminated neighbours).
* **Offline ring detection** (:func:`detect_rings`) builds a real NetworkX graph
  over the customer/device/IP/merchant projection and runs connected-component
  and centrality analysis.

The relational representation (``device_links`` + indexed transaction columns) is
the default store.  ``NEO4J_URI`` switches the projection to Neo4j via
``app.services.graph_neo4j`` when the advanced deployment profile is used.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models.core import Customer, Device, DeviceLink, Merchant, Transaction
from app.db.models.risk import FraudRing, FraudRingMember
from app.utils import clamp, safe_float

logger = get_logger(__name__)

RING_MIN_MEMBERS = 3
RING_LOOKBACK_DAYS = 90
SHARED_IP_MIN_ACCOUNTS = 3


@dataclass
class GraphRisk:
    score: float
    signals: list[dict[str, Any]] = field(default_factory=list)
    neighbours: dict[str, Any] = field(default_factory=dict)
    computation_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "signals": self.signals,
            "neighbours": self.neighbours,
            "computation_ms": round(self.computation_ms, 3),
        }


def _customers_on_device(db: Session, device_id: str) -> list[str]:
    stmt = select(DeviceLink.customer_id).where(DeviceLink.device_id == device_id)
    return [row for row in db.execute(stmt).scalars()]


def _fraud_customers(db: Session, customer_ids: Iterable[str]) -> list[str]:
    ids = [c for c in customer_ids]
    if not ids:
        return []
    stmt = select(Customer.id).where(
        Customer.id.in_(ids),
        (Customer.confirmed_fraud_count > 0) | (Customer.watchlisted.is_(True)),
    )
    return [row for row in db.execute(stmt).scalars()]


def _accounts_on_ip(db: Session, ip_address: str, before: Any) -> int:
    cutoff = before - timedelta(days=30)
    stmt = select(func.count(func.distinct(Transaction.customer_id))).where(
        Transaction.ip_address == ip_address,
        Transaction.occurred_at >= cutoff,
    )
    return int(db.execute(stmt).scalar_one() or 0)


def graph_risk(
    db: Session | None,
    *,
    customer_id: str,
    device_id: str | None,
    ip_address: str | None,
    merchant_id: str | None,
    now: Any | None = None,
    device_customers: list[str] | None = None,
    ip_account_count: int | None = None,
    contaminated: list[str] | None = None,
    merchant: Merchant | None = None,
    known_ring_id: str | None = None,
) -> GraphRisk:
    """Neighbourhood risk for one transaction, in [0, 1].

    The keyword overrides let the batch backfill supply neighbourhood state it
    already holds in memory, so the identical scoring logic runs in both paths.
    """
    started = time.perf_counter()
    now = now or utcnow()
    signals: list[dict[str, Any]] = []
    neighbours: dict[str, Any] = {}
    score = 0.0
    offline = db is None

    if device_id:
        shared = (
            device_customers
            if device_customers is not None
            else (_customers_on_device(db, device_id) if db is not None else [])
        )
        neighbours["device_customers"] = shared[:25]
        if len(shared) > 1:
            contribution = clamp((len(shared) - 1) * 0.12, 0.0, 0.42)
            score += contribution
            signals.append(
                {
                    "type": "SHARED_DEVICE",
                    "detail": f"Device {device_id} is linked to {len(shared)} customers.",
                    "weight": round(contribution, 4),
                    "entities": shared[:10],
                }
            )
        contaminated = (
            contaminated
            if contaminated is not None
            else (
                _fraud_customers(db, [c for c in shared if c != customer_id])
                if db is not None
                else []
            )
        )
        if contaminated:
            contribution = clamp(0.18 + 0.10 * len(contaminated), 0.0, 0.40)
            score += contribution
            neighbours["contaminated_customers"] = contaminated[:10]
            signals.append(
                {
                    "type": "CONTAMINATED_NEIGHBOUR",
                    "detail": (
                        f"{len(contaminated)} customer(s) on this device have confirmed fraud "
                        "or are watchlisted."
                    ),
                    "weight": round(contribution, 4),
                    "entities": contaminated[:10],
                }
            )
        device = db.get(Device, device_id) if db is not None else None
        if device and device.is_blacklisted:
            score += 0.30
            signals.append(
                {
                    "type": "BLACKLISTED_DEVICE",
                    "detail": f"Device {device_id} is blacklisted.",
                    "weight": 0.30,
                    "entities": [device_id],
                }
            )

    if ip_address:
        accounts = (
            ip_account_count
            if ip_account_count is not None
            else (_accounts_on_ip(db, ip_address, now) if db is not None else 0)
        )
        neighbours["ip_account_count"] = accounts
        if accounts >= SHARED_IP_MIN_ACCOUNTS:
            contribution = clamp((accounts - 2) * 0.08, 0.0, 0.30)
            score += contribution
            signals.append(
                {
                    "type": "SHARED_IP",
                    "detail": f"IP {ip_address} was used by {accounts} distinct accounts in 30 days.",
                    "weight": round(contribution, 4),
                    "entities": [ip_address],
                }
            )

    if merchant_id:
        merchant = merchant or (db.get(Merchant, merchant_id) if db is not None else None)
        if merchant and merchant.high_risk_flag:
            contribution = clamp(safe_float(merchant.fraud_rate) * 4.0, 0.05, 0.25)
            score += contribution
            signals.append(
                {
                    "type": "HIGH_RISK_MERCHANT",
                    "detail": (
                        f"Merchant {merchant.name} carries a "
                        f"{merchant.fraud_rate * 100:.2f}% historical fraud rate."
                    ),
                    "weight": round(contribution, 4),
                    "entities": [merchant_id],
                }
            )

    ring_hit = known_ring_id
    if ring_hit is None and not offline:
        ring_hit = db.execute(
            select(FraudRingMember.ring_id)
            .where(
                FraudRingMember.entity_type == "CUSTOMER",
                FraudRingMember.entity_id == customer_id,
            )
            .limit(1)
        ).scalar_one_or_none()
    if ring_hit:
        score += 0.35
        neighbours["fraud_ring_id"] = ring_hit
        signals.append(
            {
                "type": "KNOWN_FRAUD_RING",
                "detail": f"Customer belongs to detected fraud ring {ring_hit}.",
                "weight": 0.35,
                "entities": [ring_hit],
            }
        )

    return GraphRisk(
        score=round(clamp(score), 6),
        signals=signals,
        neighbours=neighbours,
        computation_ms=(time.perf_counter() - started) * 1000,
    )


# ----------------------------------------------------------------- projection


def _networkx():
    import networkx as nx  # imported lazily

    return nx


def build_projection(db: Session, *, days: int = RING_LOOKBACK_DAYS, limit: int = 20_000) -> Any:
    """Build the customer / device / IP / merchant graph for offline analysis."""
    nx = _networkx()
    graph = nx.Graph()
    cutoff = utcnow() - timedelta(days=days)

    stmt = (
        select(
            Transaction.id,
            Transaction.customer_id,
            Transaction.device_id,
            Transaction.ip_address,
            Transaction.merchant_id,
            Transaction.amount,
            Transaction.is_fraud,
            Transaction.risk_score,
        )
        .where(Transaction.occurred_at >= cutoff)
        .order_by(Transaction.occurred_at.desc())
        .limit(limit)
    )

    for _txn_id, customer_id, device_id, ip, merchant_id, amount, is_fraud, risk in db.execute(
        stmt
    ):
        graph.add_node(f"customer:{customer_id}", kind="CUSTOMER", entity_id=customer_id)
        graph.add_node(f"merchant:{merchant_id}", kind="MERCHANT", entity_id=merchant_id)
        if device_id:
            graph.add_node(f"device:{device_id}", kind="DEVICE", entity_id=device_id)
            _bump_edge(
                graph,
                f"customer:{customer_id}",
                f"device:{device_id}",
                amount,
                is_fraud,
                "USES_DEVICE",
            )
        if ip:
            graph.add_node(f"ip:{ip}", kind="IP", entity_id=ip)
            _bump_edge(graph, f"customer:{customer_id}", f"ip:{ip}", amount, is_fraud, "USES_IP")
        _bump_edge(
            graph,
            f"customer:{customer_id}",
            f"merchant:{merchant_id}",
            amount,
            is_fraud,
            "TRANSACTS_WITH",
        )
        node = graph.nodes[f"customer:{customer_id}"]
        node["transaction_count"] = node.get("transaction_count", 0) + 1
        node["total_amount"] = node.get("total_amount", 0.0) + float(amount or 0)
        node["fraud_count"] = node.get("fraud_count", 0) + (1 if is_fraud else 0)
        node["max_risk"] = max(node.get("max_risk", 0.0), safe_float(risk))
    return graph


def _bump_edge(graph: Any, left: str, right: str, amount: float, is_fraud: Any, kind: str) -> None:
    if graph.has_edge(left, right):
        edge = graph[left][right]
        edge["weight"] += 1
        edge["amount"] += float(amount or 0)
        edge["fraud_count"] += 1 if is_fraud else 0
    else:
        graph.add_edge(
            left,
            right,
            weight=1,
            amount=float(amount or 0),
            fraud_count=1 if is_fraud else 0,
            kind=kind,
        )


def neighbourhood(
    db: Session, entity_type: str, entity_id: str, *, depth: int = 2, max_nodes: int = 220
) -> dict[str, Any]:
    """Ego network around an entity, shaped for the frontend graph component."""
    nx = _networkx()
    graph = build_projection(db)
    root = f"{entity_type.lower()}:{entity_id}"
    if root not in graph:
        return {"nodes": [], "edges": [], "root": root, "found": False}

    ego = nx.ego_graph(graph, root, radius=max(1, min(depth, 3)))
    if ego.number_of_nodes() > max_nodes:
        ranked = sorted(
            ego.degree, key=lambda item: item[1], reverse=True  # type: ignore[arg-type]
        )
        keep = {root} | {node for node, _ in ranked[:max_nodes]}
        ego = ego.subgraph(keep).copy()

    centrality = nx.degree_centrality(ego) if ego.number_of_nodes() > 1 else {}
    nodes = []
    for node_id, data in ego.nodes(data=True):
        kind = data.get("kind", "UNKNOWN")
        risk = _node_risk(db, kind, data.get("entity_id", ""), data)
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "entity_id": data.get("entity_id"),
                "label": _node_label(db, kind, data.get("entity_id", "")),
                "risk_score": round(risk, 2),
                "risk_band": risk_band(risk),
                "degree": ego.degree(node_id),
                "centrality": round(centrality.get(node_id, 0.0), 4),
                "transaction_count": data.get("transaction_count", 0),
                "total_amount": round(data.get("total_amount", 0.0), 2),
                "fraud_count": data.get("fraud_count", 0),
                "is_root": node_id == root,
            }
        )
    edges = [
        {
            "source": left,
            "target": right,
            "kind": data.get("kind", "LINK"),
            "weight": data.get("weight", 1),
            "amount": round(data.get("amount", 0.0), 2),
            "fraud_count": data.get("fraud_count", 0),
        }
        for left, right, data in ego.edges(data=True)
    ]
    return {
        "root": root,
        "found": True,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "density": round(nx.density(ego), 5) if ego.number_of_nodes() > 1 else 0.0,
        },
    }


def risk_band(score: float) -> str:
    if score >= 85:
        return "CRITICAL"
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _node_risk(db: Session, kind: str, entity_id: str, data: dict[str, Any]) -> float:
    if kind == "CUSTOMER":
        customer = db.get(Customer, entity_id)
        return safe_float(customer.risk_score if customer else data.get("max_risk", 0.0))
    if kind == "MERCHANT":
        merchant = db.get(Merchant, entity_id)
        return safe_float(merchant.risk_score if merchant else 0.0)
    if kind == "DEVICE":
        device = db.get(Device, entity_id)
        return safe_float(device.risk_score * 100 if device else 0.0)
    if kind == "IP":
        return min(float(data.get("fraud_count", 0)) * 25.0, 100.0)
    return 0.0


def _node_label(db: Session, kind: str, entity_id: str) -> str:
    if kind == "MERCHANT":
        merchant = db.get(Merchant, entity_id)
        return merchant.name if merchant else entity_id
    if kind == "CUSTOMER":
        customer = db.get(Customer, entity_id)
        return customer.full_name if customer else entity_id
    return entity_id


# -------------------------------------------------------------- ring detection


def detect_rings(
    db: Session,
    *,
    min_members: int = RING_MIN_MEMBERS,
    days: int = RING_LOOKBACK_DAYS,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Find suspicious clusters and (optionally) persist them as fraud rings.

    A candidate cluster is a connected component of the customer graph induced by
    *shared infrastructure* (device or IP).  Components are scored on size,
    density, fraud contamination and value concentration; only those above the
    risk floor are recorded.
    """
    nx = _networkx()
    started = time.perf_counter()
    graph = build_projection(db, days=days)

    # Project onto customers: two customers are connected when they share a
    # device or an IP address.
    customer_graph = nx.Graph()
    shared_by_component: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"devices": set(), "ips": set()}
    )

    for node_id, data in graph.nodes(data=True):
        if data.get("kind") not in {"DEVICE", "IP"}:
            continue
        customers = [
            neighbour
            for neighbour in graph.neighbors(node_id)
            if graph.nodes[neighbour].get("kind") == "CUSTOMER"
        ]
        if len(customers) < 2:
            continue
        for i, left in enumerate(customers):
            customer_graph.add_node(left)
            for right in customers[i + 1 :]:
                customer_graph.add_edge(left, right)
                key = "devices" if data.get("kind") == "DEVICE" else "ips"
                shared_by_component[left][key].add(data.get("entity_id", ""))
                shared_by_component[right][key].add(data.get("entity_id", ""))

    results: list[dict[str, Any]] = []
    now = utcnow()

    for component in nx.connected_components(customer_graph):
        if len(component) < min_members:
            continue
        subgraph = customer_graph.subgraph(component)
        customer_ids = [node.split(":", 1)[1] for node in component]

        devices: set[str] = set()
        ips: set[str] = set()
        for node in component:
            devices |= shared_by_component[node]["devices"]
            ips |= shared_by_component[node]["ips"]

        agg = db.execute(
            select(
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.amount), 0.0),
                func.coalesce(func.sum(cast(Transaction.is_fraud, Integer)), 0),
                func.coalesce(func.avg(Transaction.risk_score), 0.0),
            ).where(
                Transaction.customer_id.in_(customer_ids),
                Transaction.occurred_at >= now - timedelta(days=days),
            )
        ).one()
        txn_count, total_amount, fraud_txns, avg_risk = (
            int(agg[0] or 0),
            float(agg[1] or 0.0),
            int(agg[2] or 0),
            float(agg[3] or 0.0),
        )

        merchants = [
            row
            for row in db.execute(
                select(Transaction.merchant_id, func.count(Transaction.id))
                .where(Transaction.customer_id.in_(customer_ids))
                .group_by(Transaction.merchant_id)
                .order_by(func.count(Transaction.id).desc())
                .limit(5)
            )
        ]
        shared_merchants = [m for m, _ in merchants]

        density = nx.density(subgraph) if subgraph.number_of_nodes() > 1 else 0.0
        fraud_ratio = fraud_txns / txn_count if txn_count else 0.0
        size_signal = clamp((len(component) - 2) / 8.0)
        device_signal = clamp(
            (len(devices) and 1.0) * min(len(component) / max(len(devices), 1) / 4.0, 1.0)
        )
        risk = (
            35.0 * size_signal
            + 25.0 * clamp(fraud_ratio * 5)
            + 20.0 * clamp(density)
            + 10.0 * device_signal
            + 10.0 * clamp(avg_risk / 100.0)
        )
        risk = round(clamp(risk, 0.0, 100.0), 2)
        if risk < 35.0:
            continue

        centrality = nx.degree_centrality(subgraph)
        ring = {
            "label": (
                f"Cluster of {len(component)} accounts sharing {len(devices)} device(s)"
                if devices
                else f"Cluster of {len(component)} accounts sharing {len(ips)} IP address(es)"
            ),
            "detection_method": "SHARED_DEVICE" if devices else "SHARED_IP",
            "members": customer_ids,
            "member_count": len(customer_ids),
            "transaction_count": txn_count,
            "total_amount": round(total_amount, 2),
            "fraud_probability": round(clamp(fraud_ratio * 3 + risk / 300.0), 4),
            "risk_score": risk,
            "shared_devices": sorted(devices)[:20],
            "shared_ips": sorted(ips)[:20],
            "shared_merchants": shared_merchants,
            "density": round(density, 4),
            "centrality": {
                node.split(":", 1)[1]: round(value, 4) for node, value in centrality.items()
            },
            "avg_transaction_risk": round(avg_risk, 2),
            "fraud_transactions": fraud_txns,
        }
        results.append(ring)

    results.sort(key=lambda r: r["risk_score"], reverse=True)

    if persist:
        _persist_rings(db, results)

    logger.info(
        "ring_detection_completed",
        extra={
            "candidates": len(results),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return results


def _persist_rings(db: Session, rings: list[dict[str, Any]]) -> None:
    """Upsert detected rings, keyed by their member set."""
    existing = {
        frozenset(m.entity_id for m in ring.members if m.entity_type == "CUSTOMER"): ring
        for ring in db.execute(select(FraudRing)).scalars()
    }
    now = utcnow()
    for payload in rings:
        key = frozenset(payload["members"])
        record = existing.get(key)
        if record is None:
            record = FraudRing(
                id=new_id("RING"),
                detected_at=now,
                label=payload["label"],
                detection_method=payload["detection_method"],
                member_count=payload["member_count"],
                risk_score=payload["risk_score"],
            )
            db.add(record)
            db.flush()
            for member_id in payload["members"]:
                db.add(
                    FraudRingMember(
                        id=f"{record.id}::{member_id}",
                        ring_id=record.id,
                        entity_type="CUSTOMER",
                        entity_id=member_id,
                        centrality=payload["centrality"].get(member_id, 0.0),
                        risk_contribution=round(
                            payload["risk_score"] / max(len(payload["members"]), 1), 2
                        ),
                    )
                )
        record.label = payload["label"]
        record.detection_method = payload["detection_method"]
        record.member_count = payload["member_count"]
        record.transaction_count = payload["transaction_count"]
        record.total_amount = payload["total_amount"]
        record.fraud_probability = payload["fraud_probability"]
        record.risk_score = payload["risk_score"]
        record.shared_devices = payload["shared_devices"]
        record.shared_ips = payload["shared_ips"]
        record.shared_merchants = payload["shared_merchants"]
        record.density = payload["density"]
        record.evidence = {
            "centrality": payload["centrality"],
            "avg_transaction_risk": payload["avg_transaction_risk"],
            "fraud_transactions": payload["fraud_transactions"],
        }
