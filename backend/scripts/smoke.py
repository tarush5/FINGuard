"""End-to-end API smoke test.

Exercises the real application through FastAPI's TestClient against whatever
database ``DATABASE_URL`` points at (the seeded SQLite file by default), and
prints a pass/fail line per endpoint. Run it after seeding:

    python -m scripts.smoke
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.datagen.seed import DEMO_PASSWORD
from app.main import app

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, response: Any, *, expect: int = 200, probe: Any = None) -> Any:
    ok = response.status_code == expect
    body: Any = None
    try:
        body = response.json()
    except Exception:
        body = response.text[:200]
    if ok and probe is not None:
        try:
            ok = bool(probe(body))
        except Exception as exc:
            ok = False
            body = f"probe error: {exc}"
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(
            (name, f"status={response.status_code} body={json.dumps(body, default=str)[:300]}")
        )
        print(
            f"  FAIL  {name}: status={response.status_code} {json.dumps(body, default=str)[:200]}"
        )
    return body


def main() -> int:
    with TestClient(app) as client:
        print("\n[health]")
        check("GET /health", client.get("/health"))
        check("GET /ready", client.get("/ready"), probe=lambda b: b["checks"]["database"]["ok"])
        check("GET /metrics", client.get("/metrics"))
        check(
            "GET /openapi.json", client.get("/openapi.json"), probe=lambda b: len(b["paths"]) > 50
        )

        print("\n[auth]")
        check(
            "POST /auth/login (bad password)",
            client.post(
                "/api/v1/auth/login",
                json={"email": "admin@finguard.io", "password": "wrong-password"},
            ),
            expect=401,
        )
        login = check(
            "POST /auth/login",
            client.post(
                "/api/v1/auth/login",
                json={"email": "admin@finguard.io", "password": DEMO_PASSWORD},
            ),
            probe=lambda b: "access_token" in b,
        )
        token = login["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        check("GET /auth/me", client.get("/api/v1/auth/me", headers=headers))
        check("GET /transactions (no token)", client.get("/api/v1/transactions"), expect=401)
        refreshed = check(
            "POST /auth/refresh",
            client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}),
            probe=lambda b: b["access_token"] != token,
        )
        check(
            "POST /auth/refresh (reuse blocked)",
            client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}),
            expect=401,
        )
        headers = {"Authorization": f"Bearer {refreshed['access_token']}"}

        print("\n[transactions]")
        txns = check(
            "GET /transactions",
            client.get("/api/v1/transactions?page_size=5", headers=headers),
            probe=lambda b: len(b["items"]) == 5 and b["pagination"]["total"] > 100,
        )
        txn_id = txns["items"][0]["id"]
        check(
            "GET /transactions/{id}", client.get(f"/api/v1/transactions/{txn_id}", headers=headers)
        )
        check(
            "GET /transactions/{id}/trace",
            client.get(f"/api/v1/transactions/{txn_id}/trace", headers=headers),
            probe=lambda b: len(b["stages"]) == 6,
        )
        check(
            "GET /transactions/{id}/explain",
            client.get(f"/api/v1/transactions/{txn_id}/explain", headers=headers),
            probe=lambda b: "explanation" in b,
        )
        check(
            "GET /transactions/missing",
            client.get("/api/v1/transactions/TXN-DOES-NOT-EXIST", headers=headers),
            expect=404,
            probe=lambda b: b["error"]["code"] == "TRANSACTION_NOT_FOUND",
        )

        customer_id = txns["items"][0]["customer_id"]
        merchant_id = txns["items"][0]["merchant_id"]
        ingest = check(
            "POST /transactions (ingest)",
            client.post(
                "/api/v1/transactions",
                headers=headers,
                json={
                    "event_id": "evt_smoke_test_001",
                    "customer_id": customer_id,
                    "merchant_id": merchant_id,
                    "amount": 250000,
                    "currency": "INR",
                    "channel": "WEB",
                    "device_id": "D-SMOKETEST01",
                    "ip_address": "203.0.113.44",
                    "country": "GB",
                    "city": "London",
                    "latitude": 51.5074,
                    "longitude": -0.1278,
                },
            ),
            probe=lambda b: b["decision"] in {"APPROVE", "STEP_UP", "MANUAL_REVIEW", "DECLINE"},
        )
        check(
            "POST /transactions (idempotent replay)",
            client.post(
                "/api/v1/transactions",
                headers=headers,
                json={
                    "event_id": "evt_smoke_test_001",
                    "customer_id": customer_id,
                    "merchant_id": merchant_id,
                    "amount": 250000,
                },
            ),
            probe=lambda b: b["duplicate"] is True
            and b["transaction_id"] == ingest["transaction_id"],
        )
        check(
            "POST /transactions (validation)",
            client.post(
                "/api/v1/transactions",
                headers=headers,
                json={"customer_id": customer_id, "merchant_id": merchant_id, "amount": -5},
            ),
            expect=422,
        )

        print("\n[entities]")
        check("GET /customers", client.get("/api/v1/customers?page_size=3", headers=headers))
        check(
            "GET /customers/{id}",
            client.get(f"/api/v1/customers/{customer_id}", headers=headers),
            probe=lambda b: "statistics" in b,
        )
        check("GET /merchants", client.get("/api/v1/merchants?page_size=3", headers=headers))
        check(
            "GET /merchants/{id}", client.get(f"/api/v1/merchants/{merchant_id}", headers=headers)
        )
        check("GET /devices", client.get("/api/v1/devices?shared_only=true", headers=headers))

        print("\n[risk]")
        smoke_rule_code = f"R-SMOKE-{uuid.uuid4().hex[:6].upper()}"
        rules = check(
            "GET /rules",
            client.get("/api/v1/rules", headers=headers),
            probe=lambda b: len(b["items"]) > 10 and len(b["available_fields"]) > 20,
        )
        rule_id = rules["items"][0]["id"]
        check("GET /rules/{id}", client.get(f"/api/v1/rules/{rule_id}", headers=headers))
        created = check(
            "POST /rules",
            client.post(
                "/api/v1/rules",
                headers=headers,
                json={
                    "code": smoke_rule_code,
                    "name": "Smoke test rule",
                    "description": "Created by the smoke test.",
                    "category": "AMOUNT",
                    "severity": "LOW",
                    "condition": {"field": "amount", "op": "gt", "value": 999999},
                    "risk_points": 5,
                    "action": "SCORE",
                    "priority": 500,
                },
            ),
            expect=201,
        )
        check(
            "POST /rules (bad field rejected)",
            client.post(
                "/api/v1/rules",
                headers=headers,
                json={
                    "code": f"{smoke_rule_code}-B",
                    "name": "Invalid rule",
                    "condition": {"field": "definitely_not_a_field", "op": "gt", "value": 1},
                },
            ),
            expect=422,
        )
        check(
            "PATCH /rules/{id}",
            client.patch(
                f"/api/v1/rules/{created['id']}", headers=headers, json={"risk_points": 7}
            ),
            probe=lambda b: b["risk_points"] == 7 and b["version"] == 2,
        )
        check(
            "POST /rules/test",
            client.post(
                "/api/v1/rules/test",
                headers=headers,
                json={
                    "condition": {"field": "amount_ratio_to_avg", "op": "gt", "value": 4},
                    "sample_size": 500,
                },
            ),
            probe=lambda b: "hit_rate_pct" in b,
        )
        check(
            "DELETE /rules/{id}",
            client.delete(f"/api/v1/rules/{created['id']}", headers=headers),
        )
        check("GET /risk/policy", client.get("/api/v1/risk/policy", headers=headers))
        check(
            "POST /risk/simulate",
            client.post(
                "/api/v1/risk/simulate",
                headers=headers,
                json={
                    "approve_below": 20,
                    "step_up_below": 55,
                    "review_below": 75,
                    "sample_size": 1500,
                },
            ),
            probe=lambda b: "impact" in b and b["sample_size"] > 0,
        )
        check(
            "GET /risk/threshold-optimisation",
            client.get("/api/v1/risk/threshold-optimisation?sample_size=2000", headers=headers),
            probe=lambda b: b["optimal"] is not None and len(b["curve"]) > 10,
        )

        print("\n[fraud]")
        check("GET /alerts", client.get("/api/v1/alerts?page_size=5", headers=headers))
        cases = check(
            "GET /cases",
            client.get("/api/v1/cases?page_size=5", headers=headers),
            probe=lambda b: b["pagination"]["total"] > 0,
        )
        case_id = cases["items"][0]["id"]
        check(
            "GET /cases/{id}",
            client.get(f"/api/v1/cases/{case_id}", headers=headers),
            probe=lambda b: "timeline" in b and "transaction" in b,
        )
        check(
            "GET /cases/{id}/timeline",
            client.get(f"/api/v1/cases/{case_id}/timeline", headers=headers),
        )
        check(
            "POST /cases/{id}/notes",
            client.post(
                f"/api/v1/cases/{case_id}/notes",
                headers=headers,
                json={"body": "Smoke test note."},
            ),
            expect=201,
        )
        rings = check("GET /fraud-rings", client.get("/api/v1/fraud-rings", headers=headers))
        if rings["items"]:
            check(
                "GET /fraud-rings/{id}",
                client.get(f"/api/v1/fraud-rings/{rings['items'][0]['id']}", headers=headers),
            )
        check("GET /graph/summary", client.get("/api/v1/graph/summary", headers=headers))
        check(
            "GET /graph/customer/{id}",
            client.get(f"/api/v1/graph/customer/{customer_id}?depth=2", headers=headers),
            probe=lambda b: len(b["nodes"]) > 0,
        )

        print("\n[analytics]")
        check(
            "GET /analytics/overview",
            client.get("/api/v1/analytics/overview", headers=headers),
            probe=lambda b: len(b["kpis"]) == 7,
        )
        check(
            "GET /analytics/timeseries",
            client.get("/api/v1/analytics/timeseries?days=14", headers=headers),
        )
        check(
            "GET /analytics/breakdown/channel",
            client.get("/api/v1/analytics/breakdown/channel", headers=headers),
        )
        check(
            "GET /analytics/breakdown/bogus",
            client.get("/api/v1/analytics/breakdown/bogus", headers=headers),
            expect=422,
        )
        check("GET /analytics/losses", client.get("/api/v1/analytics/losses", headers=headers))
        check(
            "GET /analytics/performance",
            client.get("/api/v1/analytics/performance", headers=headers),
        )
        check(
            "GET /analytics/merchants", client.get("/api/v1/analytics/merchants", headers=headers)
        )
        check(
            "GET /analytics/customers", client.get("/api/v1/analytics/customers", headers=headers)
        )
        check("GET /analytics/heatmap", client.get("/api/v1/analytics/heatmap", headers=headers))
        check(
            "GET /analytics/geography", client.get("/api/v1/analytics/geography", headers=headers)
        )
        check(
            "GET /analytics/operations", client.get("/api/v1/analytics/operations", headers=headers)
        )
        check(
            "GET /forecasting/transactions",
            client.get("/api/v1/forecasting/transactions", headers=headers),
            probe=lambda b: b["status"] == "OK" and "7d" in b["forecasts"],
        )
        check(
            "GET /forecasting-workload", client.get("/api/v1/forecasting-workload", headers=headers)
        )

        print("\n[mlops]")
        models = check(
            "GET /models",
            client.get("/api/v1/models", headers=headers),
            probe=lambda b: len(b["items"]) >= 3,
        )
        model_id = models["items"][0]["id"]
        check(
            "GET /models/{id}",
            client.get(f"/api/v1/models/{model_id}", headers=headers),
            probe=lambda b: "curves" in b,
        )
        check("GET /experiments", client.get("/api/v1/experiments", headers=headers))
        check(
            "GET /monitoring/models",
            client.get("/api/v1/monitoring/models", headers=headers),
            probe=lambda b: len(b["models"]) == 3,
        )
        check("GET /monitoring/drift", client.get("/api/v1/monitoring/drift", headers=headers))
        check("GET /monitoring/system", client.get("/api/v1/monitoring/system", headers=headers))
        check("GET /monitoring/latency", client.get("/api/v1/monitoring/latency", headers=headers))
        check("GET /feedback", client.get("/api/v1/feedback", headers=headers))

        print("\n[platform]")
        check("GET /datasets", client.get("/api/v1/datasets", headers=headers))
        check(
            "GET /datasets/transactions_enriched",
            client.get("/api/v1/datasets/transactions_enriched", headers=headers),
        )
        check("GET /pipelines", client.get("/api/v1/pipelines", headers=headers))
        check(
            "GET /quality",
            client.get("/api/v1/quality", headers=headers),
            probe=lambda b: b["trust_score"] > 0,
        )
        check(
            "GET /lineage",
            client.get("/api/v1/lineage", headers=headers),
            probe=lambda b: len(b["nodes"]) > 5 and len(b["edges"]) > 5,
        )
        check("GET /events/topics", client.get("/api/v1/events/topics", headers=headers))
        check("GET /events/dead-letter", client.get("/api/v1/events/dead-letter", headers=headers))

        print("\n[ai]")
        check("GET /ai/status", client.get("/api/v1/ai/status", headers=headers))
        check(
            "POST /ai/ask",
            client.post(
                "/api/v1/ai/ask",
                headers=headers,
                json={"question": "Why was this transaction flagged?", "transaction_id": txn_id},
            ),
            probe=lambda b: len(b["answer"]) > 50 and "evidence" in b,
        )
        check(
            "POST /ai/sql",
            client.post(
                "/api/v1/ai/sql",
                headers=headers,
                json={"question": "which merchants have the highest fraud rate"},
            ),
            probe=lambda b: b["status"] == "OK" and b["row_count"] > 0,
        )
        check(
            "POST /ai/sql (injection blocked)",
            client.post(
                "/api/v1/ai/sql",
                headers=headers,
                json={"question": "drop it", "sql": "DROP TABLE transactions"},
            ),
            expect=400,
            probe=lambda b: b["error"]["code"] == "UNSAFE_QUERY",
        )
        check(
            "POST /ai/cases/{id}/summary",
            client.post(f"/api/v1/ai/cases/{case_id}/summary", headers=headers),
            probe=lambda b: len(b["summary"]) > 40,
        )
        check(
            "POST /ai/cases/{id}/report",
            client.post(f"/api/v1/ai/cases/{case_id}/report", headers=headers),
        )
        check("GET /ai/queries", client.get("/api/v1/ai/queries", headers=headers))

        print("\n[governance]")
        check("GET /users", client.get("/api/v1/users", headers=headers))
        check("GET /governance/roles", client.get("/api/v1/governance/roles", headers=headers))
        check(
            "GET /governance/policies", client.get("/api/v1/governance/policies", headers=headers)
        )
        check(
            "GET /governance/ai-usage", client.get("/api/v1/governance/ai-usage", headers=headers)
        )
        check(
            "GET /audit",
            client.get("/api/v1/audit?page_size=5", headers=headers),
            probe=lambda b: b["pagination"]["total"] > 0,
        )
        check(
            "GET /notifications", client.get("/api/v1/notifications?page_size=5", headers=headers)
        )

        print("\n[rbac]")
        exec_login = client.post(
            "/api/v1/auth/login",
            json={"email": "exec@finguard.io", "password": DEMO_PASSWORD},
        ).json()
        exec_headers = {"Authorization": f"Bearer {exec_login['access_token']}"}
        check(
            "EXECUTIVE cannot write rules",
            client.post(
                "/api/v1/rules",
                headers=exec_headers,
                json={
                    "code": "R-NOPE-001",
                    "name": "Should be blocked",
                    "condition": {"field": "amount", "op": "gt", "value": 1},
                },
            ),
            expect=403,
            probe=lambda b: b["error"]["code"] == "PERMISSION_DENIED",
        )
        check(
            "EXECUTIVE cannot read the audit trail",
            client.get("/api/v1/audit", headers=exec_headers),
            expect=403,
        )
        check(
            "EXECUTIVE sees masked PII",
            client.get(f"/api/v1/customers/{customer_id}", headers=exec_headers),
            probe=lambda b: "*" in (b["customer"]["email"] or "") and b["customer"]["pii_masked"],
        )
        investigator_login = client.post(
            "/api/v1/auth/login",
            json={"email": "investigator@finguard.io", "password": DEMO_PASSWORD},
        ).json()
        inv_headers = {"Authorization": f"Bearer {investigator_login['access_token']}"}
        check(
            "FRAUD_INVESTIGATOR sees unmasked PII",
            client.get(f"/api/v1/customers/{customer_id}", headers=inv_headers),
            probe=lambda b: "@" in b["customer"]["email"] and "*" not in b["customer"]["email"],
        )

        print("\n[demo scenarios]")
        check("GET /demo/scenarios", client.get("/api/v1/demo/scenarios", headers=headers))
        for scenario in ("account_takeover", "fraud_ring", "card_testing", "false_positive"):
            check(
                f"POST /demo/run ({scenario})",
                client.post(
                    "/api/v1/demo/run", headers=headers, json={"scenario": scenario, "intensity": 1}
                ),
                probe=lambda b: len(b.get("transactions", [])) > 0,
            )

    print("\n" + "=" * 70)
    print(f"PASSED: {len(PASSED)}    FAILED: {len(FAILED)}")
    for name, detail in FAILED:
        print(f"  - {name}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
