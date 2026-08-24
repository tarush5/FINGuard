"""FINGuard hourly freshness and backlog DAG.

A light guard-rail loop: confirm ingestion is fresh, surface any dead-lettered
events, and alert when the investigation backlog breaches its SLA.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import requests
from airflow.decorators import dag, task

API_URL = os.environ.get("FINGUARD_API_URL", "http://api:8000")
API_EMAIL = os.environ.get("FINGUARD_API_EMAIL", "engineer@finguard.io")
API_PASSWORD = os.environ.get("FINGUARD_API_PASSWORD", "")


def _headers() -> dict[str, str]:
    response = requests.post(
        f"{API_URL}/api/v1/auth/login",
        json={"email": API_EMAIL, "password": API_PASSWORD},
        timeout=30,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@dag(
    dag_id="finguard_hourly_health",
    description="Freshness, dead-letter backlog and SLA monitoring",
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "data-platform", "retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["finguard", "monitoring", "hourly"],
)
def finguard_hourly() -> None:
    @task
    def check_freshness() -> dict[str, Any]:
        health = requests.get(
            f"{API_URL}/api/v1/monitoring/system", headers=_headers(), timeout=60
        ).json()
        transactions = health["throughput"]["transactions_last_hour"]
        if health["status"] == "CRITICAL":
            raise ValueError(f"System health is CRITICAL: {health['components']}")
        return {"status": health["status"], "transactions_last_hour": transactions}

    @task
    def check_dead_letters() -> dict[str, Any]:
        body = requests.get(
            f"{API_URL}/api/v1/events/dead-letter",
            headers=_headers(),
            params={"status": "FAILED", "page_size": 50},
            timeout=60,
        ).json()
        failed = body["pagination"]["total"]
        if failed:
            # Surface, do not auto-replay: a poison message must be looked at.
            print(f"::warning:: {failed} dead-lettered event(s) awaiting review")
        return {"dead_lettered": failed}

    @task
    def check_case_sla() -> dict[str, Any]:
        operations = requests.get(
            f"{API_URL}/api/v1/analytics/operations", headers=_headers(), timeout=60
        ).json()
        if operations["sla_breached"] > 25:
            raise ValueError(f"{operations['sla_breached']} cases are past their SLA")
        return {"sla_breached": operations["sla_breached"]}

    check_freshness()
    check_dead_letters()
    check_case_sla()


finguard_hourly()
