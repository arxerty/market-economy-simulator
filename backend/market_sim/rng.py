"""Deterministic randomness.

Every random draw in the simulator comes from a ``random.Random`` derived from the
run seed plus a stable label, so results never depend on scheduling order (e.g. the
order in which concurrent LLM calls complete) or on Python's salted ``hash()``.
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(seed: int, *labels: object) -> int:
    key = ":".join([str(seed), *map(str, labels)]).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def derive_rng(seed: int, *labels: object) -> random.Random:
    return random.Random(derive_seed(seed, *labels))


def draw(dist: object, rng: random.Random) -> float:
    """Draw a value from a :class:`market_sim.config.Distribution` (or a bare number)."""
    if isinstance(dist, (int, float)):
        return float(dist)
    kind = dist.dist  # type: ignore[attr-defined]
    if kind == "fixed":
        v = dist.value  # type: ignore[attr-defined]
    elif kind == "uniform":
        v = rng.uniform(dist.low, dist.high)  # type: ignore[attr-defined]
    elif kind == "normal":
        v = rng.gauss(dist.mean, dist.std)  # type: ignore[attr-defined]
    elif kind == "choice":
        v = rng.choice(dist.values)  # type: ignore[attr-defined]
    else:  # pragma: no cover - guarded by pydantic
        raise ValueError(f"unknown distribution {kind}")
    if dist.min is not None:  # type: ignore[attr-defined]
        v = max(v, dist.min)  # type: ignore[attr-defined]
    return round(float(v), 2)
