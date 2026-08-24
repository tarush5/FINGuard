"""Event bus with two interchangeable drivers.

* ``KafkaBus`` -- used whenever ``KAFKA_BROKERS`` is configured.  Real producer
  and consumer group, one background consumer thread per topic.
* ``InProcessBus`` -- the local/test driver.  Same delivery semantics: async
  dispatch on worker threads, bounded retries with exponential backoff, and a
  dead-letter table for messages that exhaust them.

Both drivers expose identical metrics (published / processed / failed / lag) so
the observability screen does not care which one is running.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger, get_request_id, set_request_id
from app.events.schemas import (
    ALL_TOPICS,
    RETRY_POLICY,
    EventEnvelope,
    Topic,
    dead_letter_topic,
)

logger = get_logger(__name__)

Handler = Callable[[EventEnvelope], None]


@dataclass
class TopicStats:
    published: int = 0
    processed: int = 0
    failed: int = 0
    dead_lettered: int = 0
    retries: int = 0
    total_latency_ms: float = 0.0
    last_published_at: float | None = None
    last_processed_at: float | None = None
    recent_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def snapshot(self, pending: int) -> dict[str, Any]:
        processed = self.processed or 1
        latencies = sorted(self.recent_latencies)

        def pct(p: float) -> float:
            if not latencies:
                return 0.0
            idx = min(int(len(latencies) * p), len(latencies) - 1)
            return round(latencies[idx], 2)

        lag_seconds = 0.0
        if pending and self.last_published_at:
            lag_seconds = round(max(time.time() - self.last_published_at, 0.0), 3)
        return {
            "published": self.published,
            "processed": self.processed,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
            "retries": self.retries,
            "pending": pending,
            "consumer_lag": pending,
            "lag_seconds": lag_seconds,
            "avg_handler_ms": round(self.total_latency_ms / processed, 2),
            "p95_handler_ms": pct(0.95),
            "p99_handler_ms": pct(0.99),
        }


class EventBus(Protocol):
    driver: str

    def publish(self, event: EventEnvelope) -> None: ...
    def subscribe(self, topic: Topic | str, handler: Handler) -> None: ...
    def start(self) -> None: ...
    def stop(self, timeout: float = 5.0) -> None: ...
    def stats(self) -> dict[str, Any]: ...
    def healthy(self) -> bool: ...
    def drain(self, timeout: float = 5.0) -> bool: ...


class _BaseBus:
    driver = "base"

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._stats: dict[str, TopicStats] = defaultdict(TopicStats)
        self._running = False
        self._lock = threading.RLock()

    def subscribe(self, topic: Topic | str, handler: Handler) -> None:
        with self._lock:
            self._handlers[str(topic)].append(handler)
        logger.info(
            "handler_subscribed",
            extra={"topic": str(topic), "handler": getattr(handler, "__name__", "handler")},
        )

    # ------------------------------------------------------------------ core
    def _dispatch(self, event: EventEnvelope) -> None:
        """Run every handler for a topic with retries, then dead-letter."""
        handlers = list(self._handlers.get(event.topic, ()))
        if not handlers:
            self._stats[event.topic].processed += 1
            return
        max_attempts, backoff_ms = RETRY_POLICY.get(Topic(event.topic), (3, 200))
        token = set_request_id(event.correlation_id or get_request_id())
        for handler in handlers:
            attempt = 0
            while True:
                attempt += 1
                started = time.perf_counter()
                try:
                    handler(event)
                    elapsed = (time.perf_counter() - started) * 1000
                    stats = self._stats[event.topic]
                    stats.processed += 1
                    stats.total_latency_ms += elapsed
                    stats.recent_latencies.append(elapsed)
                    stats.last_processed_at = time.time()
                    break
                except Exception as exc:
                    stats = self._stats[event.topic]
                    stats.failed += 1
                    handler_name = getattr(handler, "__name__", "handler")
                    if attempt >= max_attempts:
                        stats.dead_lettered += 1
                        logger.error(
                            "event_dead_lettered",
                            extra={
                                "topic": event.topic,
                                "event_id": event.event_id,
                                "handler": handler_name,
                                "attempts": attempt,
                                "error": str(exc),
                            },
                        )
                        self._dead_letter(event, handler_name, attempt, exc)
                        break
                    stats.retries += 1
                    logger.warning(
                        "event_handler_retry",
                        extra={
                            "topic": event.topic,
                            "event_id": event.event_id,
                            "handler": handler_name,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
                    time.sleep((backoff_ms * (2 ** (attempt - 1))) / 1000.0)
        set_request_id(token)

    def _dead_letter(
        self, event: EventEnvelope, handler_name: str, attempts: int, exc: Exception
    ) -> None:
        """Persist an exhausted event so it can be inspected and replayed."""
        try:
            from app.db.base import new_id
            from app.db.models.core import DeadLetterEvent
            from app.db.session import session_scope

            with session_scope() as db:
                db.add(
                    DeadLetterEvent(
                        id=new_id("DLQ"),
                        event_id=event.event_id,
                        topic=dead_letter_topic(event.topic),
                        attempts=attempts,
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:2000],
                        payload=json.loads(event.to_json()),
                        status="FAILED",
                    )
                )
        except Exception as inner:
            logger.error(
                "dead_letter_persist_failed",
                extra={"event_id": event.event_id, "error": str(inner)},
            )

    def stats(self) -> dict[str, Any]:
        raise NotImplementedError

    def healthy(self) -> bool:
        return self._running


class InProcessBus(_BaseBus):
    """Threaded in-memory broker used when Kafka is not configured."""

    driver = "in-process"

    def __init__(self, workers_per_topic: int = 1, max_queue: int = 20_000) -> None:
        super().__init__()
        self._queues: dict[str, queue.Queue[EventEnvelope | None]] = {
            str(topic): queue.Queue(maxsize=max_queue) for topic in ALL_TOPICS
        }
        self._threads: list[threading.Thread] = []
        self._workers_per_topic = workers_per_topic

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for topic, q in self._queues.items():
            for index in range(self._workers_per_topic):
                thread = threading.Thread(
                    target=self._consume,
                    args=(topic, q),
                    name=f"bus-{topic}-{index}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
        logger.info(
            "event_bus_started", extra={"driver": self.driver, "threads": len(self._threads)}
        )

    def _consume(self, topic: str, q: queue.Queue[EventEnvelope | None]) -> None:
        while True:
            event = q.get()
            try:
                if event is None:
                    return
                self._dispatch(event)
            finally:
                q.task_done()

    def publish(self, event: EventEnvelope) -> None:
        q = self._queues.setdefault(event.topic, queue.Queue(maxsize=20_000))
        stats = self._stats[event.topic]
        stats.published += 1
        stats.last_published_at = time.time()
        if not event.correlation_id:
            event.correlation_id = get_request_id()
        try:
            q.put_nowait(event)
        except queue.Full:
            stats.failed += 1
            logger.error("event_queue_full", extra={"topic": event.topic})
            raise

    def drain(self, timeout: float = 5.0) -> bool:
        """Block until every queue is empty -- used by tests and the seeder."""
        deadline = time.time() + timeout
        for q in self._queues.values():
            while not q.empty() and time.time() < deadline:
                time.sleep(0.01)
        # Give in-flight handlers a moment to finish.
        time.sleep(0.05)
        return all(q.empty() for q in self._queues.values())

    def stop(self, timeout: float = 5.0) -> None:
        if not self._running:
            return
        self.drain(timeout)
        self._running = False
        for q in self._queues.values():
            for _ in range(self._workers_per_topic):
                q.put(None)
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        logger.info("event_bus_stopped", extra={"driver": self.driver})

    def stats(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "running": self._running,
            "topics": {
                topic: self._stats[topic].snapshot(self._queues[topic].qsize())
                for topic in self._queues
            },
        }


class KafkaBus(_BaseBus):
    """kafka-python driver: real producer plus one consumer thread per topic."""

    driver = "kafka"

    def __init__(self, brokers: str) -> None:
        super().__init__()
        from kafka import KafkaProducer  # imported lazily

        self._brokers = [b.strip() for b in brokers.split(",") if b.strip()]
        self._producer = KafkaProducer(
            bootstrap_servers=self._brokers,
            client_id=settings.kafka_client_id,
            value_serializer=lambda v: v.encode("utf-8"),
            key_serializer=lambda k: (k or "").encode("utf-8"),
            acks="all",
            retries=3,
            linger_ms=5,
            max_in_flight_requests_per_connection=5,
        )
        self._consumers: list[Any] = []
        self._threads: list[threading.Thread] = []

    def publish(self, event: EventEnvelope) -> None:
        stats = self._stats[event.topic]
        if not event.correlation_id:
            event.correlation_id = get_request_id()
        self._producer.send(
            event.topic, key=event.partition_key or event.event_id, value=event.to_json()
        )
        stats.published += 1
        stats.last_published_at = time.time()

    def start(self) -> None:
        if self._running:
            return
        from kafka import KafkaConsumer

        self._running = True
        for topic in {t for t in self._handlers} or {str(t) for t in ALL_TOPICS}:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=self._brokers,
                group_id=settings.kafka_consumer_group,
                enable_auto_commit=False,
                auto_offset_reset="latest",
                value_deserializer=lambda v: v.decode("utf-8"),
                consumer_timeout_ms=1000,
            )
            self._consumers.append(consumer)
            thread = threading.Thread(
                target=self._consume, args=(consumer,), name=f"kafka-{topic}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        logger.info("event_bus_started", extra={"driver": self.driver, "brokers": self._brokers})

    def _consume(self, consumer: Any) -> None:
        while self._running:
            try:
                for message in consumer:
                    if not self._running:
                        break
                    try:
                        event = EventEnvelope.from_json(message.value)
                    except Exception as exc:
                        logger.error("event_deserialize_failed", extra={"error": str(exc)})
                        consumer.commit()
                        continue
                    self._dispatch(event)
                    consumer.commit()  # at-least-once; handlers are idempotent
            except Exception as exc:
                logger.error("kafka_consume_error", extra={"error": str(exc)})
                time.sleep(1.0)

    def drain(self, timeout: float = 5.0) -> bool:
        self._producer.flush(timeout=timeout)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        with contextlib.suppress(Exception):
            self._producer.flush(timeout=timeout)
            self._producer.close(timeout=timeout)
        for consumer in self._consumers:
            with contextlib.suppress(Exception):
                consumer.close()
        self._consumers.clear()
        self._threads.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "driver": self.driver,
            "running": self._running,
            "brokers": self._brokers,
            "topics": {topic: self._stats[topic].snapshot(0) for topic in self._stats},
        }

    def healthy(self) -> bool:
        try:
            return bool(self._producer.bootstrap_connected())
        except Exception:
            return False


def build_bus() -> EventBus:
    if settings.kafka_brokers:
        try:
            bus = KafkaBus(settings.kafka_brokers)
            logger.info("bus_driver_selected", extra={"driver": "kafka"})
            return bus
        except Exception as exc:
            logger.warning(
                "kafka_unavailable_falling_back",
                extra={"error": str(exc), "brokers": settings.kafka_brokers},
            )
    logger.info("bus_driver_selected", extra={"driver": "in-process"})
    return InProcessBus()


event_bus: EventBus = build_bus()
