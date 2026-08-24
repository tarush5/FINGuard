"""API integration tests: auth, RBAC, the decision path, AI guardrails and errors."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.datagen.seed import DEMO_PASSWORD
from app.db.models.core import Transaction


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        assert client.get("/health").json()["status"] == "ok"

    def test_ready_reports_dependencies(self, client: TestClient) -> None:
        body = client.get("/ready").json()
        assert body["checks"]["database"]["ok"] is True
        assert "event_bus" in body["checks"]

    def test_openapi_is_generated(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert len(schema["paths"]) > 40
        assert schema["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


class TestAuthentication:
    def test_login_and_profile(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"email": "admin@finguard.io", "password": DEMO_PASSWORD}
        )
        assert response.status_code == 200
        tokens = response.json()
        profile = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        ).json()
        assert profile["email"] == "admin@finguard.io"
        assert "system:admin" in profile["permissions"]

    def test_bad_password_is_rejected_without_leaking_existence(self, client: TestClient) -> None:
        known = client.post(
            "/api/v1/auth/login", json={"email": "admin@finguard.io", "password": "nope"}
        )
        unknown = client.post(
            "/api/v1/auth/login", json={"email": "ghost@finguard.io", "password": "nope"}
        )
        assert known.status_code == unknown.status_code == 401
        assert known.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_protected_route_requires_a_token(self, client: TestClient) -> None:
        assert client.get("/api/v1/transactions").status_code == 401

    def test_refresh_rotates_and_blocks_reuse(self, client: TestClient) -> None:
        tokens = client.post(
            "/api/v1/auth/login", json={"email": "scientist@finguard.io", "password": DEMO_PASSWORD}
        ).json()
        rotated = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert rotated.status_code == 200
        assert rotated.json()["access_token"] != tokens["access_token"]
        # Reusing the retired token is treated as compromise.
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            ).status_code
            == 401
        )


class TestRbac:
    def test_executive_cannot_write_rules(
        self, client: TestClient, executive_headers: dict
    ) -> None:
        response = client.post(
            "/api/v1/rules",
            headers=executive_headers,
            json={
                "code": "R-BLOCKED-1",
                "name": "Blocked",
                "condition": {"field": "amount", "op": "gt", "value": 1},
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    def test_executive_cannot_read_the_audit_trail(
        self, client: TestClient, executive_headers: dict
    ) -> None:
        assert client.get("/api/v1/audit", headers=executive_headers).status_code == 403

    def test_pii_is_masked_for_unprivileged_roles(
        self, client: TestClient, executive_headers: dict, investigator_headers: dict
    ) -> None:
        listing = client.get(
            "/api/v1/customers", headers=executive_headers, params={"page_size": 1}
        ).json()
        customer_id = listing["items"][0]["id"]

        masked = client.get(f"/api/v1/customers/{customer_id}", headers=executive_headers).json()[
            "customer"
        ]
        unmasked = client.get(
            f"/api/v1/customers/{customer_id}", headers=investigator_headers
        ).json()["customer"]

        assert masked["pii_masked"] is True
        assert "*" in masked["email"]
        assert unmasked["pii_masked"] is False
        assert "*" not in unmasked["email"]


class TestDecisionPath:
    def _payload(self, customer_id: str, merchant_id: str, **overrides) -> dict:
        payload = {
            "event_id": f"evt_test_{uuid.uuid4().hex}",
            "customer_id": customer_id,
            "merchant_id": merchant_id,
            "amount": 125_000,
            "currency": "INR",
            "channel": "WEB",
            "device_id": f"D-TEST{uuid.uuid4().hex[:6].upper()}",
            "ip_address": "198.51.100.7",
            "country": "GB",
            "city": "London",
            "latitude": 51.5074,
            "longitude": -0.1278,
        }
        payload.update(overrides)
        return payload

    def test_ingestion_returns_a_full_decision(
        self, client: TestClient, admin_headers: dict, sample_transaction: dict
    ) -> None:
        response = client.post(
            "/api/v1/transactions",
            headers=admin_headers,
            json=self._payload(
                sample_transaction["customer_id"], sample_transaction["merchant_id"]
            ),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] in {"APPROVE", "STEP_UP", "MANUAL_REVIEW", "DECLINE"}
        assert 0 <= body["risk_score"] <= 100
        assert len(body["trace"]["stages"]) == 6
        assert body["latency"]["total_ms"] > 0

    def test_ingestion_is_idempotent(
        self, client: TestClient, admin_headers: dict, sample_transaction: dict
    ) -> None:
        payload = self._payload(
            sample_transaction["customer_id"], sample_transaction["merchant_id"]
        )
        first = client.post("/api/v1/transactions", headers=admin_headers, json=payload).json()
        second = client.post("/api/v1/transactions", headers=admin_headers, json=payload).json()
        assert second["duplicate"] is True
        assert second["transaction_id"] == first["transaction_id"]
        assert second["risk_score"] == first["risk_score"]

    def test_invalid_amount_is_rejected(
        self, client: TestClient, admin_headers: dict, sample_transaction: dict
    ) -> None:
        response = client.post(
            "/api/v1/transactions",
            headers=admin_headers,
            json=self._payload(
                sample_transaction["customer_id"], sample_transaction["merchant_id"], amount=-10
            ),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_unknown_customer_is_rejected(
        self, client: TestClient, admin_headers: dict, sample_transaction: dict
    ) -> None:
        response = client.post(
            "/api/v1/transactions",
            headers=admin_headers,
            json=self._payload("C-DOES-NOT-EXIST", sample_transaction["merchant_id"]),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"

    def test_trace_and_explanation_are_available(
        self, client: TestClient, admin_headers: dict, sample_transaction: dict
    ) -> None:
        txn_id = sample_transaction["id"]
        trace = client.get(f"/api/v1/transactions/{txn_id}/trace", headers=admin_headers).json()
        assert [stage["stage"] for stage in trace["stages"]] == [
            "FEATURES",
            "RULES",
            "MODEL",
            "GRAPH",
            "RISK",
            "DECISION",
        ]
        explanation = client.get(
            f"/api/v1/transactions/{txn_id}/explain", headers=admin_headers
        ).json()
        assert "explanation" in explanation
        assert 0 <= explanation["probability"] <= 1


class TestErrorContract:
    def test_not_found_uses_the_error_envelope(
        self, client: TestClient, admin_headers: dict
    ) -> None:
        response = client.get("/api/v1/transactions/TXN-NOPE", headers=admin_headers)
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "TRANSACTION_NOT_FOUND"
        assert body["error"]["request_id"]

    def test_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "req_from_client"})
        assert response.headers["X-Request-ID"] == "req_from_client"

    def test_security_headers_are_present(self, client: TestClient) -> None:
        headers = client.get("/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"


class TestRuleManagement:
    def test_rule_lifecycle(self, client: TestClient, analyst_headers: dict) -> None:
        code = f"R-TEST-{uuid.uuid4().hex[:6].upper()}"
        created = client.post(
            "/api/v1/rules",
            headers=analyst_headers,
            json={
                "code": code,
                "name": "Integration test rule",
                "description": "Created by the test suite.",
                "category": "AMOUNT",
                "severity": "LOW",
                "condition": {"field": "amount", "op": "gt", "value": 10_000_000},
                "risk_points": 5,
                "action": "SCORE",
                "priority": 900,
            },
        )
        assert created.status_code == 201
        rule = created.json()
        assert rule["version"] == 1

        updated = client.patch(
            f"/api/v1/rules/{rule['id']}", headers=analyst_headers, json={"risk_points": 9}
        )
        assert updated.json()["risk_points"] == 9
        assert updated.json()["version"] == 2  # editing bumps the version

        duplicate = client.post(
            "/api/v1/rules",
            headers=analyst_headers,
            json={
                "code": code,
                "name": "Duplicate",
                "condition": {"field": "amount", "op": "gt", "value": 1},
            },
        )
        assert duplicate.status_code == 409

        assert (
            client.delete(f"/api/v1/rules/{rule['id']}", headers=analyst_headers).status_code == 200
        )

    def test_invalid_condition_is_rejected(self, client: TestClient, analyst_headers: dict) -> None:
        response = client.post(
            "/api/v1/rules",
            headers=analyst_headers,
            json={
                "code": "R-BAD-FIELD",
                "name": "Bad",
                "condition": {"field": "evil", "op": "gt", "value": 1},
            },
        )
        assert response.status_code == 422

    def test_backtest_returns_measurable_output(
        self, client: TestClient, analyst_headers: dict
    ) -> None:
        response = client.post(
            "/api/v1/rules/test",
            headers=analyst_headers,
            json={
                "condition": {"field": "amount_ratio_to_avg", "op": "gt", "value": 3},
                "sample_size": 500,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sample_size"] > 0
        assert 0 <= body["hit_rate_pct"] <= 100


class TestAiGuardrails:
    def test_status_lists_the_guardrails(self, client: TestClient, analyst_headers: dict) -> None:
        body = client.get("/api/v1/ai/status", headers=analyst_headers).json()
        assert body["grounded"] is True
        assert len(body["guardrails"]) >= 5

    def test_natural_language_query_runs(self, client: TestClient, analyst_headers: dict) -> None:
        body = client.post(
            "/api/v1/ai/sql",
            headers=analyst_headers,
            json={"question": "which merchants have the highest fraud rate"},
        ).json()
        assert body["status"] == "OK"
        assert body["sql"].lower().startswith("select")
        assert body["row_count"] >= 0

    def test_write_statements_are_blocked(self, client: TestClient, analyst_headers: dict) -> None:
        for statement in (
            "DROP TABLE transactions",
            "DELETE FROM cases",
            "UPDATE rules SET risk_points = 0",
        ):
            response = client.post(
                "/api/v1/ai/sql",
                headers=analyst_headers,
                json={"question": "run this statement", "sql": statement},
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "UNSAFE_QUERY"

    def test_multi_statement_and_comments_are_blocked(
        self, client: TestClient, analyst_headers: dict
    ) -> None:
        for statement in ("SELECT 1; DELETE FROM cases", "SELECT id FROM transactions -- comment"):
            response = client.post(
                "/api/v1/ai/sql",
                headers=analyst_headers,
                json={"question": "run this statement", "sql": statement},
            )
            assert response.status_code == 400

    def test_restricted_tables_are_blocked(self, client: TestClient, analyst_headers: dict) -> None:
        response = client.post(
            "/api/v1/ai/sql",
            headers=analyst_headers,
            json={"question": "show me the users table", "sql": "SELECT * FROM users"},
        )
        assert response.status_code == 400
        assert "cannot query" in response.json()["error"]["message"]

    def test_pii_columns_are_blocked_without_permission(
        self, client: TestClient, analyst_headers: dict
    ) -> None:
        response = client.post(
            "/api/v1/ai/sql",
            headers=analyst_headers,
            json={"question": "show customer emails", "sql": "SELECT email FROM customers LIMIT 5"},
        )
        assert response.status_code == 400
        assert "PII" in response.json()["error"]["message"]

    def test_investigator_answer_is_evidence_backed(
        self, client: TestClient, investigator_headers: dict, sample_transaction: dict
    ) -> None:
        body = client.post(
            "/api/v1/ai/ask",
            headers=investigator_headers,
            json={
                "question": "Why was this transaction flagged?",
                "transaction_id": sample_transaction["id"],
            },
        ).json()
        assert len(body["answer"]) > 40
        assert isinstance(body["evidence"], list)
        assert body["generated_by"] in {"llm", "deterministic"}
        assert "disclaimer" in body


class TestCaseWorkflow:
    def test_case_workflow_produces_feedback(
        self, client: TestClient, investigator_headers: dict, db
    ) -> None:
        cases = client.get(
            "/api/v1/cases", headers=investigator_headers, params={"status": "NEW", "page_size": 1}
        ).json()
        if not cases["items"]:
            cases = client.get(
                "/api/v1/cases", headers=investigator_headers, params={"page_size": 1}
            ).json()
        case = cases["items"][0]

        detail = client.get(f"/api/v1/cases/{case['id']}", headers=investigator_headers).json()
        assert "timeline" in detail and "transaction" in detail

        note = client.post(
            f"/api/v1/cases/{case['id']}/notes",
            headers=investigator_headers,
            json={"body": "Reviewed by the suite."},
        )
        assert note.status_code == 201

        if case["status"] not in {"CONFIRMED_FRAUD", "FALSE_POSITIVE", "RESOLVED"}:
            moved = client.patch(
                f"/api/v1/cases/{case['id']}/status",
                headers=investigator_headers,
                json={"status": "CONFIRMED_FRAUD", "notes": "Confirmed by the suite."},
            )
            assert moved.status_code == 200
            # The verdict must have produced a labelled training example.
            transaction = db.get(Transaction, case["primary_transaction_id"])
            db.refresh(transaction)
            assert transaction.is_fraud is True
            assert transaction.label_source == "analyst"

    def test_invalid_transition_is_rejected(
        self, client: TestClient, investigator_headers: dict
    ) -> None:
        resolved = client.get(
            "/api/v1/cases",
            headers=investigator_headers,
            params={"status": "CONFIRMED_FRAUD", "page_size": 1},
        ).json()
        if not resolved["items"]:
            return
        response = client.patch(
            f"/api/v1/cases/{resolved['items'][0]['id']}/status",
            headers=investigator_headers,
            json={"status": "NEW"},
        )
        assert response.status_code == 409


class TestAnalyticsAndPlatform:
    def test_overview_returns_seven_kpis(self, client: TestClient, admin_headers: dict) -> None:
        body = client.get("/api/v1/analytics/overview", headers=admin_headers).json()
        assert len(body["kpis"]) == 7
        assert all("value" in kpi and "change_pct" in kpi for kpi in body["kpis"])

    def test_unsupported_breakdown_dimension(self, client: TestClient, admin_headers: dict) -> None:
        assert (
            client.get("/api/v1/analytics/breakdown/nonsense", headers=admin_headers).status_code
            == 422
        )

    def test_loss_analytics_are_internally_consistent(
        self, client: TestClient, admin_headers: dict
    ) -> None:
        body = client.get("/api/v1/analytics/losses", headers=admin_headers).json()
        expected = (
            body["gross_fraud_loss"] + body["false_positive_cost"] + body["investigation_cost"]
        )
        assert body["net_loss"] == round(expected, 2)

    def test_quality_suite_runs(self, client: TestClient, admin_headers: dict) -> None:
        body = client.post("/api/v1/quality/run", headers=admin_headers).json()
        assert 0 <= body["trust_score"] <= 100
        assert len(body["checks"]) > 8

    def test_lineage_graph_is_connected(self, client: TestClient, admin_headers: dict) -> None:
        body = client.get("/api/v1/lineage", headers=admin_headers).json()
        assert len(body["nodes"]) > 5
        assert len(body["edges"]) > 5

    def test_simulation_replays_history(self, client: TestClient, analyst_headers: dict) -> None:
        body = client.post(
            "/api/v1/risk/simulate",
            headers=analyst_headers,
            json={"approve_below": 20, "step_up_below": 50, "review_below": 70, "sample_size": 300},
        ).json()
        assert body["sample_size"] > 0
        assert set(body["impact"]) >= {
            "expected_fraud_loss_pct",
            "false_positives_pct",
            "manual_reviews_pct",
        }


class TestDemoScenarios:
    def test_account_takeover_scenario_scores_transactions(
        self, client: TestClient, admin_headers: dict
    ) -> None:
        body = client.post(
            "/api/v1/demo/run",
            headers=admin_headers,
            json={"scenario": "account_takeover", "intensity": 1},
        ).json()
        assert len(body["transactions"]) >= 3
        # The takeover leg should out-score the ordinary baseline transaction.
        assert (
            max(t["risk_score"] for t in body["transactions"])
            > body["transactions"][0]["risk_score"]
        )

    def test_fraud_ring_scenario_shares_infrastructure(
        self, client: TestClient, admin_headers: dict
    ) -> None:
        body = client.post(
            "/api/v1/demo/run",
            headers=admin_headers,
            json={"scenario": "fraud_ring", "intensity": 1},
        ).json()
        assert body["shared_device"].startswith("D-RINGDEMO")
        assert len(body["members"]) >= 3

    def test_dead_letter_queue_stays_empty(self, client: TestClient, admin_headers: dict) -> None:
        body = client.get(
            "/api/v1/events/dead-letter", headers=admin_headers, params={"status": "FAILED"}
        ).json()
        assert body["pagination"]["total"] == 0
