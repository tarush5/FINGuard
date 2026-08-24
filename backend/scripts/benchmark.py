"""Decision-path benchmark.

Measures the real end-to-end latency of ``process_transaction`` and of the API
endpoints the UI depends on, then prints percentiles against the published
targets. Run it after seeding:

    python -m scripts.benchmark [--iterations 300]

Numbers are hardware dependent; the point is that the platform reports what it
measured rather than what it hopes for.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.datagen.seed import DEMO_PASSWORD
from app.db.models.core import Customer, Merchant
from app.db.session import session_scope
from app.main import app
from app.services.monitoring import LATENCY_TARGETS
from app.services.pipeline import TransactionInput, process_transaction

STAGE_KEYS = (
    "validation_ms",
    "dedup_ms",
    "enrichment_ms",
    "feature_ms",
    "rule_ms",
    "model_ms",
    "graph_ms",
    "persist_ms",
    "total_ms",
)


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    ordered = sorted(values)

    def at(p: float) -> float:
        return round(ordered[min(int(len(ordered) * p), len(ordered) - 1)], 3)

    return {
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "mean": round(statistics.fmean(ordered), 3),
        "max": round(ordered[-1], 3),
    }


# The first few scorings pay a one-time cost: loading the model artifact from
# disk and building the SHAP explainer. Those samples are reported separately as
# cold start rather than being blended into the steady-state percentiles.
WARMUP = 5


def benchmark_decision_path(iterations: int) -> dict[str, Any]:
    """Time the scoring pipeline directly, with no HTTP layer in the way."""
    stage_samples: dict[str, list[float]] = {key: [] for key in STAGE_KEYS}

    with session_scope() as db:
        customers = list(
            db.execute(select(Customer).where(Customer.transaction_count > 5).limit(50)).scalars()
        )
        merchants = list(db.execute(select(Merchant).limit(25)).scalars())
        if not customers or not merchants:
            raise SystemExit("Seed the database first: python -m app.datagen.seed --reset")

        for index in range(iterations):
            customer = customers[index % len(customers)]
            merchant = merchants[index % len(merchants)]
            payload = TransactionInput(
                event_id=f"evt_bench_{uuid.uuid4().hex}",
                transaction_id=f"TXN-BENCH{uuid.uuid4().hex[:12].upper()}",
                customer_id=customer.id,
                merchant_id=merchant.id,
                amount=float(customer.avg_transaction_amount or 2500) * 1.5,
                device_id=f"D-BENCH{index % 7}",
                ip_address=f"10.10.{index % 250}.5",
                latitude=customer.home_latitude,
                longitude=customer.home_longitude,
                country=customer.country,
                city=customer.city,
                is_demo=True,
            )
            # publish=False keeps the measurement on the decision path itself.
            result = process_transaction(db, payload, publish=False, commit=False)
            for key in STAGE_KEYS:
                if key in result.latency:
                    stage_samples[key].append(result.latency[key])
        db.rollback()  # the benchmark must not leave rows behind

    cold_start = round(stage_samples["total_ms"][0], 2) if stage_samples.get("total_ms") else 0.0
    warm = {
        key: percentiles(values[WARMUP:] or values)
        for key, values in stage_samples.items()
        if values
    }
    warm["_cold_start_ms"] = {"first_transaction": cold_start}
    return warm


def benchmark_api(iterations: int) -> dict[str, Any]:
    """Time the read endpoints the command centre polls."""
    results: dict[str, list[float]] = {}
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@finguard.io", "password": DEMO_PASSWORD},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        endpoints = [
            (
                "GET /transactions",
                lambda: client.get("/api/v1/transactions?page_size=25", headers=headers),
            ),
            (
                "GET /analytics/overview",
                lambda: client.get("/api/v1/analytics/overview", headers=headers),
            ),
            (
                "GET /analytics/timeseries",
                lambda: client.get("/api/v1/analytics/timeseries?days=30", headers=headers),
            ),
            ("GET /cases", lambda: client.get("/api/v1/cases?page_size=25", headers=headers)),
            (
                "GET /monitoring/system",
                lambda: client.get("/api/v1/monitoring/system", headers=headers),
            ),
        ]

        for name, call in endpoints:
            samples: list[float] = []
            for _ in range(max(iterations // 10, 10)):
                started = time.perf_counter()
                response = call()
                elapsed = (time.perf_counter() - started) * 1000
                if response.status_code == 200:
                    samples.append(elapsed)
            results[name] = samples

    return {name: percentiles(values) for name, values in results.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark the FINGuard decision path")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--json", action="store_true", help="emit machine readable output")
    args = parser.parse_args()

    started = time.perf_counter()
    decision = benchmark_decision_path(args.iterations)
    api = benchmark_api(args.iterations)
    duration = time.perf_counter() - started

    median_total = decision.get("total_ms", {}).get("p50", 0.0)
    report = {
        "iterations": args.iterations,
        "duration_seconds": round(duration, 2),
        "warmup_excluded": WARMUP,
        "decision_path_ms": decision,
        "api_ms": api,
        "targets_ms": LATENCY_TARGETS,
        "single_thread_decisions_per_second": (
            round(1000 / median_total, 1) if median_total else None
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("\nFINGuard decision path benchmark")
    print("=" * 72)
    cold = decision.pop("_cold_start_ms", {}).get("first_transaction", 0.0)
    print(
        f"{args.iterations} transactions scored in {duration:.1f}s "
        f"(first {WARMUP} excluded as warm-up; cold start {cold:.0f}ms)\n"
    )
    print(f"{'stage':<18}{'p50':>10}{'p95':>10}{'p99':>10}{'mean':>10}{'target p95':>13}")
    print("-" * 72)
    for stage, values in decision.items():
        target = LATENCY_TARGETS.get(f"decision.{stage.replace('_ms', '')}")
        target_text = f"{target:.0f}ms" if target else "--"
        if stage == "total_ms":
            target_text = f"{LATENCY_TARGETS['decision.total']:.0f}ms"
        print(
            f"{stage:<18}{values['p50']:>9.2f}m{values['p95']:>9.2f}m"
            f"{values['p99']:>9.2f}m{values['mean']:>9.2f}m{target_text:>13}"
        )

    print(f"\n{'endpoint':<28}{'p50':>10}{'p95':>10}{'p99':>10}{'target p95':>13}")
    print("-" * 72)
    for name, values in api.items():
        print(
            f"{name:<28}{values['p50']:>9.2f}m{values['p95']:>9.2f}m"
            f"{values['p99']:>9.2f}m{LATENCY_TARGETS['api.request']:>12.0f}ms"
        )

    total = decision.get("total_ms", {})
    if total:
        print(
            f"\nSingle-threaded throughput: ~{1000 / max(total['mean'], 0.001):.0f} decisions/second "
            f"on this machine."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
