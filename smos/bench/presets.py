"""Benchmark size presets. The sweep step lengths and sizes are estimates, tuned after real runs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str
    sizes: tuple[int, ...]
    step_seconds: float
    clients: tuple[int, ...]
    probe_count: int
    wall_cap_s: float


_WIDE_CLIENTS = (1, 2, 4, 8, 16, 32, 64)

PRESETS: dict[str, Preset] = {
    "quick": Preset("quick", (500, 1000, 2500, 5000), 8.0, (1, 2, 4, 8), 100, 20 * 60.0),
    "standard": Preset("standard", (1000, 5000, 10000, 25000), 15.0, _WIDE_CLIENTS, 200, 90 * 60.0),
    "full": Preset("full", (1000, 5000, 10000, 25000, 50000, 100000), 20.0, _WIDE_CLIENTS, 200, 6 * 3600.0),
}

# Tests and the verification run only; never offered on the page.
SMOKE = Preset("smoke", (40, 90), 1.5, (1, 2), 8, 10 * 60.0)
