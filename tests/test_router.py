"""Network-free tests for the sticky weighted canary router (Vertical B).
Runs under pytest OR as a plain script."""
from __future__ import annotations

from collections import Counter

from agentctl.gateway.route_cache import RouteCache
from agentctl.gateway.router import Backend, RouteTable, Router, weighted_pick


def test_canary_distribution_is_90_10():
    r = Router(RouteCache())
    n = 5000
    c = Counter(r.resolve(f"session-{i}").arm for i in range(n))
    frac_a = c["vA"] / n
    assert 0.86 <= frac_a <= 0.94, (frac_a, dict(c))
    assert set(c) == {"vA", "vB"}


def test_sticky_per_session():
    r = Router(RouteCache())
    for sid in ["x", "y", "session-42", "abc", "user-99"]:
        arms = {r.resolve(sid).arm for _ in range(10)}
        assert len(arms) == 1, (sid, arms)   # deterministic per session


def test_shadow_present():
    d = Router(RouteCache()).resolve("s1")
    assert d.shadows and d.shadows[0].version_tag == "shadow"


def test_weighted_pick_proportions():
    backends = (Backend("a", "x", 70, "a"), Backend("b", "x", 30, "b"))
    c = Counter(weighted_pick(backends, k).backend_id for k in range(10000))
    assert 0.66 <= c["a"] / 10000 <= 0.74, dict(c)


def test_single_backend_table():
    cache = RouteCache({"default": RouteTable("default", 1, (Backend("only", "x", 100, "only"),))})
    r = Router(cache)
    assert all(r.resolve(f"s{i}").arm == "only" for i in range(50))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("router tests passed")
