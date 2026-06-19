"""RouteCache - STUB for the gateway's view of the routing table (Vertical B).

In production this is fed by Postgres (Vertical C) over ControlPlane.WatchRoutes +
LISTEN/NOTIFY, with copy-on-write snapshots. Here it returns a hardcoded table. The
``snapshot()`` seam is identical, so the real cache drops in without touching the router.
"""
from __future__ import annotations

from agentctl.gateway.router import Backend, RouteTable

_DEFAULT = RouteTable(
    deployment_id="default",
    version=1,
    primary=(
        Backend("vA", "localhost:50051", 90, "vA"),
        Backend("vB", "localhost:50052", 10, "vB", is_canary=True),
    ),
    shadow=(Backend("shadow", "localhost:50053", 0, "shadow"),),
)


class RouteCache:
    def __init__(self, tables: dict[str, RouteTable] | None = None):
        self._tables = tables or {"default": _DEFAULT}

    def snapshot(self, deployment: str = "default") -> RouteTable:
        return self._tables[deployment]
