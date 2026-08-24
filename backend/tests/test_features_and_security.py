"""Feature engineering (including point-in-time correctness) and security primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AuthenticationError, ValidationError
from app.core.rbac import Permission, Role, can_view_pii, permissions_for
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    mask_email,
    mask_ip,
    mask_phone,
    verify_password,
)
from app.db.models.core import Customer, Merchant, Transaction
from app.services.features import (
    FEATURE_NAMES,
    TransactionContext,
    compute_features,
    update_customer_profile,
)
from app.utils import haversine_km, implied_speed_kmh, percentile_rank, psi


class TestFeatureComputation:
    def _context(self, customer: Customer, merchant: Merchant, **overrides) -> TransactionContext:
        base = {
            "transaction_id": "TXN-TEST",
            "customer_id": customer.id,
            "merchant_id": merchant.id,
            "amount": 5000.0,
            "occurred_at": datetime.now(UTC),
            "device_id": "D-TEST",
            "ip_address": "10.1.2.3",
            "latitude": 19.07,
            "longitude": 72.87,
            "country": "IN",
            "city": "Mumbai",
        }
        base.update(overrides)
        return TransactionContext(**base)

    def test_every_declared_feature_is_produced(self, db) -> None:
        customer = db.execute(select(Customer).limit(1)).scalar_one()
        merchant = db.execute(select(Merchant).limit(1)).scalar_one()
        fv = compute_features(
            db, self._context(customer, merchant), customer=customer, merchant=merchant
        )
        assert set(fv.values) == set(FEATURE_NAMES)
        assert len(fv.as_list()) == len(FEATURE_NAMES)

    def test_features_are_point_in_time(self, db) -> None:
        """Only history *older* than the transaction may influence its features."""
        customer = db.execute(
            select(Customer).where(Customer.transaction_count > 5).limit(1)
        ).scalar_one()
        merchant = db.execute(select(Merchant).limit(1)).scalar_one()

        earliest = db.execute(
            select(Transaction)
            .where(Transaction.customer_id == customer.id)
            .order_by(Transaction.occurred_at.asc())
            .limit(1)
        ).scalar_one()

        # Scored at the very beginning of the customer's history: no prior rows.
        before_everything = compute_features(
            db,
            self._context(
                customer,
                merchant,
                occurred_at=earliest.occurred_at.replace(tzinfo=UTC) - timedelta(days=1),
            ),
            customer=customer,
            merchant=merchant,
        )
        assert before_everything.get("txn_count_24h") == 0
        assert before_everything.context["history_size"] == 0

        # Scored now: history exists.
        now = compute_features(
            db, self._context(customer, merchant), customer=customer, merchant=merchant
        )
        assert now.context["history_size"] > 0

    def test_injected_state_matches_query_based_state(self, db) -> None:
        """The offline backfill path must produce the same vector as the online one."""
        customer = db.execute(
            select(Customer).where(Customer.transaction_count > 3).limit(1)
        ).scalar_one()
        merchant = db.execute(select(Merchant).limit(1)).scalar_one()
        context = self._context(customer, merchant)

        online = compute_features(db, context, customer=customer, merchant=merchant)
        history = list(
            db.execute(
                select(Transaction)
                .where(
                    Transaction.customer_id == customer.id,
                    Transaction.occurred_at < context.occurred_at,
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(400)
            ).scalars()
        )
        offline = compute_features(
            None,
            context,
            customer=customer,
            merchant=merchant,
            history=history,
            device_state=(
                online.get("is_new_device"),
                int(online.get("device_customer_count")),
                online.get("device_risk"),
            ),
            ip_customer_count=int(online.get("ip_customer_count")),
        )
        for name in FEATURE_NAMES:
            assert offline.get(name) == pytest.approx(online.get(name), rel=1e-6), name

    def test_amount_ratio_reflects_the_customer_profile(self, db) -> None:
        customer = db.execute(
            select(Customer).where(Customer.avg_transaction_amount > 0).limit(1)
        ).scalar_one()
        merchant = db.execute(select(Merchant).limit(1)).scalar_one()
        amount = float(customer.avg_transaction_amount) * 5
        fv = compute_features(
            db,
            self._context(customer, merchant, amount=amount),
            customer=customer,
            merchant=merchant,
        )
        assert fv.get("amount_ratio_to_avg") == pytest.approx(5.0, rel=0.02)


class TestProfileMaintenance:
    def test_running_average_and_deviation(self) -> None:
        customer = Customer(
            id="C-TEST",
            full_name="Test",
            email="t@example.test",
            onboarded_at=datetime.now(UTC),
            transaction_count=0,
            avg_transaction_amount=0.0,
            std_transaction_amount=0.0,
            max_transaction_amount=0.0,
            lifetime_value=0.0,
        )
        for amount in (100.0, 200.0, 300.0):
            update_customer_profile(customer, amount, datetime.now(UTC), "GROCERY")

        assert customer.transaction_count == 3
        assert customer.avg_transaction_amount == pytest.approx(200.0, abs=0.01)
        assert customer.std_transaction_amount == pytest.approx(100.0, abs=0.01)
        assert customer.max_transaction_amount == 300.0
        assert customer.lifetime_value == 600.0


class TestGeoMaths:
    def test_known_distance(self) -> None:
        # Mumbai -> Delhi is roughly 1150 km.
        assert haversine_km(19.0760, 72.8777, 28.6139, 77.2090) == pytest.approx(1150, rel=0.05)

    def test_missing_coordinates_return_zero(self) -> None:
        assert haversine_km(None, None, 1.0, 1.0) == 0.0

    def test_impossible_speed(self) -> None:
        # 5000 km in 10 minutes is 30,000 km/h.
        assert implied_speed_kmh(5000, 600) == pytest.approx(30000, rel=0.01)


class TestStatistics:
    def test_psi_is_zero_for_identical_distributions(self) -> None:
        values = [float(i % 50) for i in range(500)]
        score, _ = psi(values, values)
        assert score == pytest.approx(0.0, abs=1e-6)

    def test_psi_grows_when_the_distribution_shifts(self) -> None:
        baseline = [float(i % 50) for i in range(500)]
        shifted = [float(i % 50) + 40 for i in range(500)]
        score, detail = psi(baseline, shifted)
        assert score > 0.25
        assert detail["bins"]

    def test_percentile_rank(self) -> None:
        assert percentile_rank([1, 2, 3, 4], 4) == 1.0
        assert percentile_rank([1, 2, 3, 4], 2) == 0.5


class TestSecurityPrimitives:
    def test_password_round_trip(self) -> None:
        hashed = hash_password("Str0ng!Passphrase")
        assert verify_password("Str0ng!Passphrase", hashed)
        assert not verify_password("wrong", hashed)
        assert hashed != "Str0ng!Passphrase"

    def test_weak_passwords_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            hash_password("short")
        with pytest.raises(ValidationError, match="three of"):
            hash_password("alllowercaseletters")

    def test_token_round_trip(self) -> None:
        token, jti, _ = create_token("USR-1", token_type="access", roles=["ADMIN"])
        claims = decode_token(token, expected_type="access")
        assert claims["sub"] == "USR-1"
        assert claims["roles"] == ["ADMIN"]
        assert claims["jti"] == jti

    def test_token_type_is_enforced(self) -> None:
        token, _, _ = create_token("USR-1", token_type="refresh")
        with pytest.raises(AuthenticationError):
            decode_token(token, expected_type="access")

    def test_tampered_token_is_rejected(self) -> None:
        token, _, _ = create_token("USR-1", token_type="access")
        with pytest.raises(AuthenticationError):
            decode_token(token[:-4] + "AAAA", expected_type="access")

    def test_masking(self) -> None:
        assert mask_email("analyst@bank.test").endswith("@bank.test")
        assert "analyst" not in mask_email("analyst@bank.test")
        assert mask_phone("+919876543210").endswith("3210")
        assert mask_ip("10.20.30.40") == "10.20.*.*"


class TestRbac:
    def test_admin_has_every_permission(self) -> None:
        assert permissions_for([Role.ADMIN.value]) == frozenset(Permission)

    def test_executive_is_read_only(self) -> None:
        granted = permissions_for([Role.EXECUTIVE.value])
        assert Permission.ANALYTICS_READ in granted
        assert Permission.RULE_WRITE not in granted
        assert Permission.AUDIT_READ not in granted

    def test_only_privileged_roles_see_pii(self) -> None:
        assert can_view_pii([Role.FRAUD_INVESTIGATOR.value])
        assert can_view_pii([Role.ADMIN.value])
        assert not can_view_pii([Role.RISK_ANALYST.value])
        assert not can_view_pii([Role.EXECUTIVE.value])

    def test_unknown_roles_grant_nothing(self) -> None:
        assert permissions_for(["NOT_A_ROLE"]) == frozenset()
