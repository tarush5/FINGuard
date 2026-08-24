"""Event envelope and topic catalogue.

Every message on the bus is an :class:`EventEnvelope`.  The envelope carries the
identity (``event_id``) used for deduplication, a ``schema_version`` so
consumers can evolve independently, and the ``correlation_id`` that ties a whole
transaction lifecycle together across services and log lines.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Topic(StrEnum):
    TRANSACTIONS_RAW = "transactions.raw"
    TRANSACTIONS_VALIDATED = "transactions.validated"
    TRANSACTIONS_ENRICHED = "transactions.enriched"
    FRAUD_PREDICTIONS = "fraud.predictions"
    RISK_EVENTS = "risk.events"
    ALERTS_CREATED = "alerts.created"
    CASES_CREATED = "cases.created"
    ANALYST_FEEDBACK = "analyst.feedback"
    MODEL_EVENTS = "model.events"
    SYSTEM_EVENTS = "system.events"


ALL_TOPICS: tuple[Topic, ...] = tuple(Topic)

# Retry policy per topic: (max_attempts, base_backoff_ms).
RETRY_POLICY: dict[Topic, tuple[int, int]] = {
    Topic.TRANSACTIONS_RAW: (3, 200),
    Topic.TRANSACTIONS_VALIDATED: (3, 200),
    Topic.TRANSACTIONS_ENRICHED: (3, 200),
    Topic.FRAUD_PREDICTIONS: (3, 250),
    Topic.RISK_EVENTS: (3, 250),
    Topic.ALERTS_CREATED: (5, 300),
    Topic.CASES_CREATED: (5, 300),
    Topic.ANALYST_FEEDBACK: (5, 300),
    Topic.MODEL_EVENTS: (2, 500),
    Topic.SYSTEM_EVENTS: (1, 500),
}


def dead_letter_topic(topic: str) -> str:
    return f"{topic}.dlq"


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    event_type: str
    topic: str
    schema_version: str = "1.0"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
    causation_id: str | None = None
    producer: str = "finguard-api"
    partition_key: str | None = None
    attempt: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)

    def payload_hash(self) -> str:
        blob = json.dumps(self.payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> EventEnvelope:
        return cls.model_validate_json(raw)


def make_event(
    topic: Topic | str,
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
    correlation_id: str | None = None,
    partition_key: str | None = None,
    producer: str = "finguard-api",
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or f"evt_{uuid.uuid4().hex}",
        event_type=event_type,
        topic=str(topic),
        correlation_id=correlation_id,
        partition_key=partition_key,
        producer=producer,
        payload=payload,
    )
