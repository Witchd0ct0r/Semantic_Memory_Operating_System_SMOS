from __future__ import annotations

from typing import Optional

_KNEE_GROWTH = 1.10  # a step that adds less than 10% throughput has passed the knee
_MIN_PROJECTION_POINTS = 3


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100) of unsorted values."""
    if not values:
        raise ValueError("percentile of an empty list")
    ordered = sorted(values)
    position = (len(ordered) - 1) * min(max(q, 0.0), 100.0) / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize_latencies(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "n": len(ms),
        "mean": sum(ms) / len(ms),
        "p50": percentile(ms, 50),
        "p95": percentile(ms, 95),
        "p99": percentile(ms, 99),
        "max": max(ms),
    }


def _counted(steps: list[dict]) -> list[dict]:
    """Steps whose numbers describe SMOS and not the load generator."""
    return [s for s in steps if not s.get("client_limited")]


def peak_throughput(steps: list[dict]) -> Optional[dict]:
    counted = _counted(steps)
    return max(counted, key=lambda s: s["rps"]) if counted else None


def sustainable_step(steps: list[dict], budget_ms: float) -> Optional[dict]:
    """The fastest step with no errors whose p95 stays within the latency budget."""
    ok = [s for s in _counted(steps)
          if s["errors"] == 0 and s["p95_ms"] is not None and s["p95_ms"] <= budget_ms]
    return max(ok, key=lambda s: s["rps"]) if ok else None


def find_knee(steps: list[dict]) -> Optional[int]:
    """The client count after which the next step adds less than 10% throughput."""
    ordered = sorted(_counted(steps), key=lambda s: s["clients"])
    for current, following in zip(ordered, ordered[1:]):
        if current["rps"] > 0 and following["rps"] / current["rps"] < _KNEE_GROWTH:
            return int(current["clients"])
    return None


def project_size_at_budget(points: list[tuple[float, float]], budget_ms: float) -> Optional[float]:
    """Segment count at which a straight-line fit of p95 against size reaches the budget (an extrapolation)."""
    if len(points) < _MIN_PROJECTION_POINTS:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    if slope <= 0:
        return None
    intercept = mean_y - slope * mean_x
    return (budget_ms - intercept) / slope
