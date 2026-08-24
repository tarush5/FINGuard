"""Small shared helpers: geo maths, statistics and safe conversions."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

EARTH_RADIUS_KM = 6371.0088


def haversine_km(
    lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None
) -> float:
    """Great-circle distance in kilometres; 0.0 when any coordinate is missing."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = phi2 - phi1
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def implied_speed_kmh(distance_km: float, seconds: float) -> float:
    if seconds <= 0:
        return 0.0 if distance_km <= 0 else float("inf")
    return distance_km / (seconds / 3600.0)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(result) or math.isinf(result) else result


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * clamp(p, 0.0, 1.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def percentile_rank(values: Sequence[float], target: float) -> float:
    if not values:
        return 0.0
    below = sum(1 for v in values if v <= target)
    return round(below / len(values), 4)


def zscore(value: float, avg: float, sd: float) -> float:
    if sd <= 0:
        return 0.0
    return (value - avg) / sd


def psi(baseline: Sequence[float], current: Sequence[float], bins: int = 10) -> tuple[float, dict]:
    """Population Stability Index with equal-frequency bins from the baseline."""
    if len(baseline) < bins or not current:
        return 0.0, {}
    edges = [percentile(baseline, i / bins) for i in range(1, bins)]
    edges = sorted(set(edges))
    if not edges:
        return 0.0, {}

    def bucket(values: Sequence[float]) -> list[float]:
        counts = [0] * (len(edges) + 1)
        for value in values:
            index = 0
            while index < len(edges) and value > edges[index]:
                index += 1
            counts[index] += 1
        total = len(values) or 1
        return [c / total for c in counts]

    base_dist = bucket(baseline)
    curr_dist = bucket(current)
    epsilon = 1e-6
    score = 0.0
    detail_bins = []
    for index, (b, c) in enumerate(zip(base_dist, curr_dist)):
        b_adj, c_adj = max(b, epsilon), max(c, epsilon)
        contribution = (c_adj - b_adj) * math.log(c_adj / b_adj)
        score += contribution
        detail_bins.append(
            {
                "bin": index,
                "upper_edge": round(edges[index], 4) if index < len(edges) else None,
                "baseline_pct": round(b, 4),
                "current_pct": round(c, 4),
                "contribution": round(contribution, 5),
            }
        )
    return round(score, 5), {"bins": detail_bins, "edges": [round(e, 4) for e in edges]}


def ks_statistic(baseline: Sequence[float], current: Sequence[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (no SciPy dependency)."""
    if not baseline or not current:
        return 0.0
    combined = sorted(set(list(baseline) + list(current)))
    base_sorted, curr_sorted = sorted(baseline), sorted(current)
    n_b, n_c = len(base_sorted), len(curr_sorted)
    max_gap = 0.0
    i = j = 0
    for value in combined:
        while i < n_b and base_sorted[i] <= value:
            i += 1
        while j < n_c and curr_sorted[j] <= value:
            j += 1
        max_gap = max(max_gap, abs(i / n_b - j / n_c))
    return round(max_gap, 5)


def to_utc(value: datetime | None) -> datetime | None:
    """Normalise to timezone-aware UTC (SQLite hands back naive datetimes)."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def chunked(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def round_dict(data: dict[str, Any], digits: int = 4) -> dict[str, Any]:
    return {
        key: round(value, digits) if isinstance(value, float) else value
        for key, value in data.items()
    }
