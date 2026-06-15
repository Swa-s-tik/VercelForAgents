"""Per-session sticky weighted canary routing (Vertical B).

Plain dataclasses (not protos) so this — the load-bearing decision logic — is unit-testable
with no network. The canary arm is chosen ONCE per session and pinned: per-message routing
would corrupt conversational state and break approval round-trips.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    backend_id: str
    endpoint: str
    weight: int
    version_tag: str
    is_canary: bool = False


@dataclass(frozen=True)
class RouteTable:
    deployment_id: str
    version: int
    primary: tuple[Backend, ...]
    shadow: tuple[Backend, ...] = ()
    sticky: str = "STICKY_SESSION"


@dataclass(frozen=True)
class RouteDecision:
    primary: Backend
    shadows: tuple[Backend, ...]
    arm: str


def stable_hash(s: str) -> int:
    """Deterministic across processes (unlike Python's salted ``hash``)."""
    return int.from_bytes(hashlib.sha1(s.encode()).digest()[:8], "big")


def weighted_pick(backends: tuple[Backend, ...], key: int) -> Backend:
    """Pick a backend in proportion to weight, deterministically from ``key``.
    Sorted by backend_id so the cumulative ranges are stable/reproducible."""
    eligible = [b for b in backends if b.weight > 0] or list(backends)
    total = sum(b.weight for b in eligible)
    if total <= 0:
        return eligible[0]
    point = key % total
    cum = 0
    for b in sorted(eligible, key=lambda x: x.backend_id):
        cum += b.weight
        if point < cum:
            return b
    return eligible[-1]


class Router:
    def __init__(self, cache):
        self.cache = cache

    def resolve(self, session_id: str, deployment: str = "default") -> RouteDecision:
        table = self.cache.snapshot(deployment)
        # mix session with table.version so a routing change re-rolls only NEW sessions.
        key = stable_hash(session_id) ^ table.version
        arm = weighted_pick(table.primary, key)
        return RouteDecision(primary=arm, shadows=table.shadow, arm=arm.version_tag)
