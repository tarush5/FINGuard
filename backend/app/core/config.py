"""Central application configuration.

Every tunable in FINGuard is expressed here and sourced from the environment so
that the same image can run in demo, development and production modes without a
code change.  Secrets are *never* defaulted to a real value: the application
refuses to boot in production mode with a development JWT secret.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
DEV_JWT_SECRET = "dev-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # ---------------------------------------------------------------- general
    app_name: str = "FINGuard"
    api_v1_prefix: str = "/api/v1"
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = True
    # DEMO mode seeds synthetic data and labels it as such in every API payload.
    platform_mode: Literal["demo", "production"] = "demo"

    # --------------------------------------------------------------- storage
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'finguard.db').as_posix()}"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    redis_url: str | None = None  # falls back to an in-process cache
    cache_default_ttl: int = 30

    # ------------------------------------------------------------- streaming
    kafka_brokers: str | None = None  # falls back to the in-process event bus
    kafka_client_id: str = "finguard"
    kafka_consumer_group: str = "finguard-workers"
    event_max_retries: int = 3
    event_retry_backoff_ms: int = 200

    # ---------------------------------------------------------------- security
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 7
    password_min_length: int = 10
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )
    rate_limit_per_minute: int = 240
    rate_limit_auth_per_minute: int = 20
    mask_pii: bool = True

    # --------------------------------------------------------------------- ml
    model_dir: Path = BACKEND_DIR / "artifacts" / "models"
    mlflow_tracking_uri: str | None = None  # e.g. http://localhost:5000
    mlflow_experiment: str = "finguard-fraud"
    drift_psi_warning: float = 0.10
    drift_psi_critical: float = 0.25

    # ------------------------------------------------------------- decisioning
    decision_approve_below: float = 30.0
    decision_stepup_below: float = 70.0
    decision_review_below: float = 85.0
    cost_false_negative: float = 50_000.0
    cost_false_positive: float = 500.0
    cost_manual_review: float = 150.0
    currency: str = "INR"

    # --------------------------------------------------------------------- ai
    llm_provider: Literal["openai", "anthropic", "none"] = "none"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30
    llm_max_output_tokens: int = 1200
    ai_sql_row_limit: int = 500

    # --------------------------------------------------------------- graph db
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None

    # --------------------------------------------------------- object storage
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "finguard"

    # ------------------------------------------------------------- demo seeds
    # When true, an empty database is seeded on first boot. Intended for
    # ephemeral demo deployments where no shell is available; the seeder is
    # idempotent and skips entirely if any transaction already exists.
    seed_on_startup: bool = False
    seed_customers: int = 900
    seed_merchants: int = 140
    seed_transactions: int = 24_000
    seed_fraud_rate: float = 0.011
    seed_random_state: int = 20260824

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, value: object) -> object:
        """Accept the URL shapes hosting providers hand out.

        Render, Heroku and Fly all expose ``postgres://HOST/DB``; SQLAlchemy 2.0 only
        recognises ``postgresql://``. Rewriting here means the deployment can
        reference the provider's variable directly instead of hand-editing it.
        """
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return value.replace("postgres://", "postgresql+psycopg2://", 1)
            if value.startswith("postgresql://"):
                return value.replace("postgresql://", "postgresql+psycopg2://", 1)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a JSON array or a comma separated list from the environment."""
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                return json.loads(raw)
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value

    @field_validator("model_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> object:
        return Path(value) if isinstance(value, str) else value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def demo_mode(self) -> bool:
        return self.platform_mode == "demo"

    @property
    def sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def validated(self) -> Settings:
        """Fail fast on insecure production configuration."""
        if self.is_production:
            problems: list[str] = []
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be set to a value of >=32 chars")
            if self.sqlite:
                problems.append("DATABASE_URL must point at PostgreSQL in production")
            if self.debug:
                problems.append("DEBUG must be false in production")
            if problems:
                raise RuntimeError("Refusing to start in production mode: " + "; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().validated()


def new_secret(nbytes: int = 48) -> str:
    return secrets.token_urlsafe(nbytes)


settings = get_settings()
