"""In-process observability.

Latency and counter samples are kept in bounded ring buffers and exposed both as
JSON (for the System Health screen) and in Prometheus text format, so the same
numbers drive the UI and any external scraper.  Percentiles are computed from
the retained window -- the API says so explicitly rather than implying they cover
all time.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

WINDOW = 2000


@dataclass
class Histogram:
    name: str
    unit: str = "ms"
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    count: int = 0
    total: float = 0.0

    def observe(self, value: float) -> None:
        self.samples.append(value)
        self.count += 1
        self.total += value

    def percentile(self, p: float) -> float:
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(int(len(ordered) * p), len(ordered) - 1)
        return round(ordered[index], 3)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "unit": self.unit,
            "count": self.count,
            "window": len(self.samples),
            "avg": round(self.total / self.count, 3) if self.count else 0.0,
            "p50": self.percentile(0.50),
            "p95": self.percentile(0.95),
            "p99": self.percentile(0.99),
            "max": round(max(self.samples), 3) if self.samples else 0.0,
        }


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._histograms: dict[str, Histogram] = {}
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._started_at = time.time()

    def observe(self, name: str, value: float, unit: str = "ms") -> None:
        with self._lock:
            hist = self._histograms.get(name)
            if hist is None:
                hist = Histogram(name=name, unit=unit)
                self._histograms[name] = hist
            hist.observe(value)

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def histogram(self, name: str) -> dict[str, Any]:
        with self._lock:
            hist = self._histograms.get(name)
            return hist.snapshot() if hist else Histogram(name=name).snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "counters": {key: round(value, 3) for key, value in self._counters.items()},
                "gauges": dict(self._gauges),
                "latencies": {name: hist.snapshot() for name, hist in self._histograms.items()},
            }

    def prometheus(self) -> str:
        with self._lock:
            lines: list[str] = []
            for key, value in self._counters.items():
                metric = _sanitise(key)
                lines.append(f"# TYPE finguard_{metric} counter")
                lines.append(f"finguard_{metric} {value}")
            for key, value in self._gauges.items():
                metric = _sanitise(key)
                lines.append(f"# TYPE finguard_{metric} gauge")
                lines.append(f"finguard_{metric} {value}")
            for name, hist in self._histograms.items():
                metric = _sanitise(name)
                lines.append(f"# TYPE finguard_{metric}_ms summary")
                lines.append(f'finguard_{metric}_ms{{quantile="0.5"}} {hist.percentile(0.5)}')
                lines.append(f'finguard_{metric}_ms{{quantile="0.95"}} {hist.percentile(0.95)}')
                lines.append(f'finguard_{metric}_ms{{quantile="0.99"}} {hist.percentile(0.99)}')
                lines.append(f"finguard_{metric}_ms_count {hist.count}")
                lines.append(f"finguard_{metric}_ms_sum {round(hist.total, 3)}")
            return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._histograms.clear()
            self._counters.clear()
            self._gauges.clear()


def _sanitise(name: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in name).strip("_").lower()


metrics = MetricsRegistry()


class Timer:
    """Context manager that records elapsed milliseconds into a histogram."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.elapsed_ms = 0.0
        self._started = 0.0

    def __enter__(self) -> Timer:
        self._started = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._started) * 1000
        metrics.observe(self.name, self.elapsed_ms)


# Latency budgets published on the performance dashboard. These are targets --
# the UI compares them against measured percentiles and reports the difference.
LATENCY_TARGETS: dict[str, float] = {
    "api.request": 300.0,
    "decision.total": 500.0,
    "decision.features": 120.0,
    "decision.rules": 40.0,
    "decision.model": 100.0,
    "decision.graph": 80.0,
    "decision.persist": 150.0,
}
