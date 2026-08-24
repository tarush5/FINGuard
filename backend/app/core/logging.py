"""Structured JSON logging with request correlation.

A ``request_id`` is generated (or accepted from the ``X-Request-ID`` header) by
the middleware and propagated through a :class:`~contextvars.ContextVar` so that
every log line emitted while handling a request — including from services and
event consumers — carries the same correlation id.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor: ContextVar[str | None] = ContextVar("actor", default=None)

_RESERVED = frozenset(
    [
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    ]
)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def set_request_id(value: str | None) -> str:
    value = value or new_request_id()
    _request_id.set(value)
    return value


def get_request_id() -> str | None:
    return _request_id.get()


def set_actor(value: str | None) -> None:
    _actor.set(value)


def get_actor() -> str | None:
    return _actor.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if rid := get_request_id():
            payload["request_id"] = rid
        if actor := get_actor():
            payload["actor"] = actor
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
