"""Postgres-backed routing cache with LISTEN/NOTIFY invalidation (Phase 3).

Replaces the static RouteCache: the live routing table is loaded from Postgres and a
background worker holds a dedicated `LISTEN routing_changed` connection. When a deployment
status or canary target shifts (Vertical C's flip transaction fires `pg_notify` inside the
commit), the worker reloads and atomically swaps the in-memory snapshot. Because the gateway
pins a session's arm at open time (sticky), a swap only affects NEW sessions - active gRPC
streams are never dropped.

Implements the same `snapshot()` seam as RouteCache, so Router/GatewayServicer are unchanged.
"""
from __future__ import annotations

import asyncio
import threading

import psycopg
from psycopg.rows import dict_row

from agentctl.config import DEMO_PROJECT_ID, PG_DSN
from agentctl.gateway.router import Backend, RouteTable

_LIVE_QUERY = """
    SELECT rr.deployment_id, rr.weight, rr.is_canary, rr.shadow_target,
           rt.version, d.git_commit_sha, d.build_meta
    FROM controlplane.routing_tables rt
    JOIN controlplane.routing_rules rr ON rr.routing_table_id = rt.id
    JOIN controlplane.deployments d    ON d.id = rr.deployment_id
    WHERE rt.project_id = %s AND rt.is_live
    ORDER BY rr.weight DESC
"""


class PgRouteCache:
    def __init__(self, project_id: str = DEMO_PROJECT_ID, dsn: str = PG_DSN,
                 deployment_key: str = "default"):
        self.project_id = project_id
        self.dsn = dsn
        self.deployment_key = deployment_key
        self.reloads = 0
        self._table = self._load_table()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._aconn: psycopg.AsyncConnection | None = None
        self._atask: asyncio.Task | None = None

    def _load_table(self) -> RouteTable:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(_LIVE_QUERY, [self.project_id])
                rows = cur.fetchall()
        version = rows[0]["version"] if rows else 0
        primary, shadow = [], []
        for r in rows:
            bm = r["build_meta"] or {}
            backend = Backend(
                backend_id=str(r["deployment_id"]),
                endpoint=bm.get("endpoint", "localhost:50051"),
                weight=r["weight"],
                version_tag=bm.get("version_tag") or r["git_commit_sha"][:6],
                is_canary=r["is_canary"])
            (shadow if r["shadow_target"] else primary).append(backend)
        return RouteTable(deployment_id=self.deployment_key, version=version,
                          primary=tuple(primary), shadow=tuple(shadow))

    # ---- the RouteCache seam ----------------------------------------------
    def snapshot(self, deployment: str = "default") -> RouteTable:
        return self._table          # atomic reference read (GIL-safe)

    def reload(self) -> None:
        self._table = self._load_table()   # atomic reference swap (copy-on-write)
        self.reloads += 1

    def _live_version(self) -> int:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT coalesce(max(version),0) AS v FROM controlplane.routing_tables "
                            "WHERE project_id=%s AND is_live", [self.project_id])
                return cur.fetchone()["v"]

    def reload_if_changed(self) -> bool:
        if self._live_version() != self._table.version:
            self.reload()
            return True
        return False

    # ---- LISTEN/NOTIFY worker ---------------------------------------------
    def start_watching(self) -> None:
        self._thread = threading.Thread(target=self._watch, name="pg-route-watch", daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        """Hold a dedicated LISTEN connection and reload on each NOTIFY. If the connection drops (DB
        restart/failover/idle kill) the loop reconnects with capped backoff and reloads on reconnect to
        catch any NOTIFY missed while disconnected - so the gateway can never get silently stuck serving
        a stale routing table after a transient DB blip."""
        backoff = 0.5
        while not self._stop.is_set():
            try:
                conn = psycopg.connect(self.dsn, autocommit=True)
                try:
                    conn.execute("LISTEN routing_changed")
                    self.reload()            # catch up on changes missed while (re)connecting
                    backoff = 0.5            # connected cleanly -> reset backoff
                    while not self._stop.is_set():
                        got = False
                        for _ in conn.notifies(timeout=1.0):
                            got = True
                        if got:
                            self.reload()
                finally:
                    conn.close()
            except Exception:
                if self._stop.is_set():
                    break
                self._stop.wait(backoff)     # back off, but wake immediately on stop_watching()
                backoff = min(backoff * 2, 30.0)

    def stop_watching(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    # ---- asyncio-native watcher (preferred inside the async gateway) -------
    async def start_async_watching(self) -> None:
        """LISTEN on a psycopg AsyncConnection, integrated into the gateway's event loop
        (no cross-thread GIL contention). Establishes LISTEN synchronously (awaited) before
        returning, so there is no missed-notify race, then iterates notifies in a task."""
        self._aconn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
        await self._aconn.execute("LISTEN routing_changed")
        self._atask = asyncio.create_task(self._aiterate())

    async def _aiterate(self) -> None:
        # NOTIFY fast-path + version-poll backstop (the PRD's LISTEN/NOTIFY + slow-poll design):
        # notifies(timeout) wakes immediately on a notify and at most every `poll_s` otherwise;
        # on each wake we reload only if the live routing version actually changed. If the LISTEN
        # connection drops, reconnect with capped backoff (and re-LISTEN) instead of dying silently -
        # the version poll on the next wake then catches up on anything missed while disconnected.
        poll_s = 0.5
        backoff = 0.5
        try:
            while not self._stop.is_set():
                try:
                    if self._aconn is None or self._aconn.closed:
                        self._aconn = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
                        await self._aconn.execute("LISTEN routing_changed")
                        await asyncio.to_thread(self.reload_if_changed)   # catch up after reconnect
                    backoff = 0.5                                         # connected cleanly
                    while not self._stop.is_set():
                        async for _ in self._aconn.notifies(timeout=poll_s):
                            break                                        # got a notify -> reload now
                        await asyncio.to_thread(self.reload_if_changed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if self._aconn is not None:
                        try:
                            await self._aconn.close()
                        except Exception:
                            pass
                    self._aconn = None
                    if self._stop.is_set():
                        break
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        except asyncio.CancelledError:
            pass
        finally:
            if self._aconn is not None:
                try:
                    await self._aconn.close()
                except Exception:
                    pass

    async def stop_async_watching(self) -> None:
        self._stop.set()
        if self._atask:
            self._atask.cancel()
            try:
                await self._atask
            except (asyncio.CancelledError, Exception):
                pass
