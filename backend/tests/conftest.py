"""Test fixtures.

Every test runs against a throwaway SQLite database seeded with a small
synthetic portfolio, so the suite exercises the real schema, the real decision
path and the real API — no mocks of our own code.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Configure the environment *before* app modules import settings.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="finguard-tests-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PLATFORM_MODE", "demo")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-the-suite-only-0123456789")
os.environ.setdefault("MODEL_DIR", str(_TMP_DIR / "models"))
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("RATE_LIMIT_AUTH_PER_MINUTE", "100000")
os.environ.setdefault("LLM_PROVIDER", "none")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.datagen.seed import DEMO_PASSWORD, seed  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded_database() -> Iterator[dict]:
    """Seed a small portfolio once for the whole session."""
    summary = seed(
        reset=True,
        customers=60,
        merchants=15,
        transactions=1200,
        days=45,
        train=False,  # training needs more positives than this fixture generates
        quiet=True,
    )
    yield summary


@pytest.fixture()
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "admin@finguard.io")


@pytest.fixture(scope="session")
def analyst_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "risk.analyst@finguard.io")


@pytest.fixture(scope="session")
def executive_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "exec@finguard.io")


@pytest.fixture(scope="session")
def investigator_headers(client: TestClient) -> dict[str, str]:
    return _login(client, "investigator@finguard.io")


@pytest.fixture()
def sample_transaction(client: TestClient, admin_headers: dict[str, str]) -> dict:
    response = client.get("/api/v1/transactions", headers=admin_headers, params={"page_size": 1})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "the seeded database should contain transactions"
    return items[0]
