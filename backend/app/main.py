"""FINGuard API application factory."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.cache import cache
from app.core.config import settings
from app.core.errors import error_payload, register_exception_handlers
from app.core.logging import configure_logging, get_logger, set_actor, set_request_id
from app.db.session import create_all, ping
from app.events.bus import event_bus
from app.events.consumers import register_all
from app.services.monitoring import metrics

logger = get_logger(__name__)

API_DESCRIPTION = """
Real-time financial crime detection, risk decisioning and investigation platform.

**Decision path**: validate -> deduplicate -> enrich -> features -> rules ->
model -> graph -> ensemble -> decide -> persist -> publish.

All endpoints require a bearer access token from `POST /api/v1/auth/login`
except the health probes and this documentation. Authorisation is
permission-based (see `GET /api/v1/auth/roles`), and personally identifiable
information is masked for roles without `customer:pii_read`.
"""

# Paths that must work without a token.
PUBLIC_PATHS = frozenset({"/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json", "/"})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(json_output=not settings.debug)
    logger.info(
        "starting",
        extra={
            "environment": settings.environment,
            "mode": settings.platform_mode,
            "database": settings.database_url.split("@")[-1],
            "cache": cache.name,
            "bus": event_bus.driver,
        },
    )
    if settings.sqlite:
        # Local/dev convenience; deployed environments run Alembic migrations.
        create_all()
    if not ping():
        logger.error("database_unreachable_at_startup")

    register_all()
    event_bus.start()
    try:
        yield
    finally:
        event_bus.stop()
        logger.info("stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        description=API_DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "FINGuard Platform Team"},
        license_info={"name": "MIT"},
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        """Correlation id, timing, rate limiting and security headers."""
        request_id = set_request_id(request.headers.get("x-request-id"))
        set_actor(None)
        started = time.perf_counter()

        limited = _rate_limit(request)
        if limited is not None:
            return limited

        try:
            response = await call_next(request)
        except Exception:
            metrics.increment("api.errors")
            raise

        elapsed_ms = (time.perf_counter() - started) * 1000
        metrics.observe("api.request", elapsed_ms)
        metrics.increment("api.requests")
        if response.status_code >= 500:
            metrics.increment("api.errors")

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    register_exception_handlers(app)

    from app.api.v1 import (
        ai,
        analytics,
        demo,
        entities,
        fraud,
        governance,
        mlops,
        platform,
        risk,
        system,
        transactions,
    )
    from app.api.v1 import auth as auth_router

    prefix = settings.api_v1_prefix
    app.include_router(system.router, prefix=prefix)
    app.include_router(auth_router.router, prefix=prefix)
    app.include_router(transactions.router, prefix=prefix)
    app.include_router(entities.router, prefix=prefix)
    app.include_router(risk.router, prefix=prefix)
    app.include_router(fraud.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(mlops.router, prefix=prefix)
    app.include_router(platform.router, prefix=prefix)
    app.include_router(ai.router, prefix=prefix)
    app.include_router(governance.router, prefix=prefix)
    app.include_router(demo.router, prefix=prefix)

    # Load balancers probe the root paths; only the probes are exposed there,
    # not the whole system router.
    app.add_api_route("/health", system.health, methods=["GET"], include_in_schema=False)
    app.add_api_route("/ready", system.ready, methods=["GET"], include_in_schema=False)
    app.add_api_route(
        "/metrics",
        system.prometheus_metrics,
        methods=["GET"],
        include_in_schema=False,
        response_class=PlainTextResponse,
    )

    @app.get("/", include_in_schema=False)
    def index() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "version": app.version,
            "mode": settings.platform_mode,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    app.openapi = _openapi(app)  # type: ignore[method-assign]
    return app


def _rate_limit(request: Request) -> JSONResponse | None:
    """Fixed-window limiter keyed on client + route class.

    Auth endpoints get a much lower ceiling because they are the credential
    stuffing surface.
    """
    path = request.url.path
    if path in PUBLIC_PATHS or request.method == "OPTIONS":
        return None

    client = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    is_auth = path.startswith(f"{settings.api_v1_prefix}/auth")
    ceiling = settings.rate_limit_auth_per_minute if is_auth else settings.rate_limit_per_minute
    window = int(time.time() // 60)
    key = f"ratelimit:{'auth' if is_auth else 'api'}:{client}:{window}"

    try:
        count = cache.backend.incr(key, ttl=90)
    except Exception:
        return None

    if count > ceiling:
        metrics.increment("api.rate_limited")
        return JSONResponse(
            status_code=429,
            content=error_payload(
                "RATE_LIMITED",
                f"Rate limit of {ceiling} requests per minute exceeded.",
                {"retry_after_seconds": 60 - int(time.time() % 60)},
            ),
            headers={"Retry-After": str(60 - int(time.time() % 60))},
        )
    return None


def _openapi(app: FastAPI) -> Any:
    def custom() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema["components"] = schema.get("components", {})
        schema["components"]["securitySchemes"] = {
            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
        schema["security"] = [{"bearerAuth": []}]
        schema["tags"] = [
            {"name": "auth", "description": "Authentication and session management"},
            {"name": "transactions", "description": "Ingestion, search and decision traces"},
            {"name": "entities", "description": "Customers, merchants and devices"},
            {"name": "risk", "description": "Rules, simulation and threshold optimisation"},
            {"name": "fraud", "description": "Alerts, cases, rings and graph intelligence"},
            {"name": "analytics", "description": "Financial, fraud and forecasting analytics"},
            {"name": "mlops", "description": "Model registry, monitoring, drift and feedback"},
            {"name": "platform", "description": "Datasets, pipelines, quality and lineage"},
            {"name": "ai", "description": "AI investigator, text-to-SQL and summarisation"},
            {"name": "governance", "description": "Users, policies and the audit trail"},
            {"name": "demo", "description": "One-click demonstration scenarios"},
            {"name": "system", "description": "Health, metrics and event operations"},
        ]
        app.openapi_schema = schema
        return schema

    return custom


app = create_app()
