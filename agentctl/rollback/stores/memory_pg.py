"""Real Postgres event-sourced memory StateStore (Workstream 1).

Same contract as MemoryGraphStub - an append-only event log with a single HEAD =
(snapshot_seq, log_offset) that rollback rewinds - backed by memorystore tables. Reuses the shared
``digest`` formula verbatim so sealed ``state_digest`` values match after restore. Bound to the
orchestrator's connection + project.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import digest


class PgMemoryStore:
    store_id = "memory:pg"

    def __init__(self, project_id: str, conn):
        self.project_id = project_id
        self.conn = conn

    # ---- functional internals ------------------------------------------------
    def append_many(self, count: int, origin_fn) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT coalesce(max(seq)+1,0) AS base FROM memorystore.event_log "
                        "WHERE project_id=%s", [self.project_id])
            base = cur.fetchone()["base"]
            cur.executemany(
                "INSERT INTO memorystore.event_log (project_id, seq, op, origin) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (project_id, seq) DO NOTHING",
                [(self.project_id, base + i, "upsert_node", origin_fn(base + i)) for i in range(count)])

    def set_head(self, snapshot_seq: int, log_offset: int, graph_id: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memorystore.head (project_id, snapshot_seq, log_offset, graph_id) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (project_id) "
                "DO UPDATE SET snapshot_seq=EXCLUDED.snapshot_seq, log_offset=EXCLUDED.log_offset",
                [self.project_id, snapshot_seq, log_offset, graph_id])

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM memorystore.event_log WHERE project_id=%s", [self.project_id])
            cur.execute("DELETE FROM memorystore.head WHERE project_id=%s", [self.project_id])

    # ---- StateStore interface (identical semantics to the stub) --------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(f"{coordinate['snapshot_seq']}:{coordinate['log_offset']}")

    def restore(self, coordinate: dict) -> str:
        self.set_head(coordinate["snapshot_seq"], coordinate["log_offset"],
                      coordinate.get("graph_id"))
        return self.live_digest()

    def live_digest(self) -> str:
        h = self.live_head()
        return digest(f"{h['snapshot_seq']}:{h['log_offset']}")

    # ---- observability -------------------------------------------------------
    def live_head(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT snapshot_seq, log_offset FROM memorystore.head WHERE project_id=%s",
                        [self.project_id])
            row = cur.fetchone()
        return {"snapshot_seq": row["snapshot_seq"] if row else None,
                "log_offset": row["log_offset"] if row else None}

    def log_size(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM memorystore.event_log WHERE project_id=%s",
                        [self.project_id])
            return cur.fetchone()["n"]

    def tombstoned_after_head(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT log_offset FROM memorystore.head WHERE project_id=%s", [self.project_id])
            row = cur.fetchone()
        return max(0, self.log_size() - (row["log_offset"] if row else 0))
