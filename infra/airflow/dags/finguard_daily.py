"""FINGuard daily risk operations DAG.

Runs the batch half of the platform once a day:

    quality suite -> risk aggregates -> ring detection -> drift -> retrain gate

Every task calls the FINGuard API rather than importing application code, so the
scheduler needs no database credentials and the same authorisation rules apply
to Airflow as to a human analyst (it authenticates as a DATA_ENGINEER service
account).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException

API_URL = os.environ.get("FINGUARD_API_URL", "http://api:8000")
API_EMAIL = os.environ.get("FINGUARD_API_EMAIL", "engineer@finguard.io")
API_PASSWORD = os.environ.get("FINGUARD_API_PASSWORD", "")
TIMEOUT = 300

DEFAULT_ARGS = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
    "depends_on_past": False,
}


def _token() -> str:
    response = requests.post(
        f"{API_URL}/api/v1/auth/login",
        json={"email": API_EMAIL, "password": API_PASSWORD},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _call(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {_token()}"}
    response = requests.request(
        method, f"{API_URL}/api/v1{path}", headers=headers, timeout=TIMEOUT, **kwargs
    )
    response.raise_for_status()
    return response.json()


@dag(
    dag_id="finguard_daily_risk_operations",
    description="Daily data quality, risk aggregation, ring detection and drift monitoring",
    schedule="0 2 * * *",  # 02:00 UTC, after the previous day has settled
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["finguard", "risk", "daily"],
)
def finguard_daily() -> None:
    @task
    def data_quality() -> dict[str, Any]:
        """Run the quality suite and fail the run if trust collapses."""
        result = _call("POST", "/quality/run")
        if result["trust_score"] < 90:
            raise ValueError(
                f"Data trust score {result['trust_score']}% is below the 90% floor: "
                f"{[c['check_name'] for c in result['failed_checks']]}"
            )
        return {"trust_score": result["trust_score"], "failed": len(result["failed_checks"])}

    @task
    def detect_fraud_rings() -> dict[str, Any]:
        result = _call("POST", "/fraud-rings/detect", params={"min_members": 3, "days": 90})
        return {"rings_detected": result["detected"]}

    @task
    def compute_drift() -> dict[str, Any]:
        result = _call("GET", "/monitoring/drift", params={"recompute": True, "window_days": 7})
        offenders = [f["feature"] for f in result.get("features", []) if f["status"] != "HEALTHY"]
        return {"status": result.get("status"), "drifting_features": offenders}

    @task
    def retraining_gate(drift: dict[str, Any]) -> dict[str, Any]:
        """Retrain only when there is a reason to: new labels or real drift."""
        monitoring = _call("GET", "/monitoring/models", params={"days": 7})
        retraining = monitoring.get("retraining", {})
        drifting = bool(drift.get("drifting_features"))

        if not retraining.get("ready") and not drifting:
            raise AirflowSkipException(
                f"No retraining needed: {retraining.get('pending_labels', 0)} pending labels, "
                "no drift above threshold."
            )
        result = _call("POST", "/models/train", json={"model": "fraud", "promote": False})
        fraud = result.get("fraud", {})
        return {
            "trained": fraud.get("tag"),
            "metrics": fraud.get("metrics"),
            "reason": "drift" if drifting else "new labels",
            "note": "Trained into STAGING; promotion stays a human decision.",
        }

    @task
    def publish_summary(quality: dict[str, Any], rings: dict[str, Any], drift: dict[str, Any]) -> str:
        summary = (
            f"trust={quality['trust_score']}% failed_checks={quality['failed']} "
            f"rings={rings['rings_detected']} drift={drift['status']}"
        )
        print(f"FINGuard daily summary :: {summary}")
        return summary

    quality = data_quality()
    rings = detect_fraud_rings()
    drift = compute_drift()

    quality >> rings >> drift
    drift >> retraining_gate(drift)
    publish_summary(quality, rings, drift)


finguard_daily()
