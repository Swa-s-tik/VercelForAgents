"""Conversational memory graph — functional mock of an event-sourced log (Phase 8).

The graph is an append-only transaction log; HEAD = (snapshot_seq, log_offset). A rollback
REWINDS HEAD to a target offset by moving the pointer — events the rolled-past deployment
wrote remain in the log (tombstoned past HEAD), so a later roll-forward can replay them.
Nothing is destroyed. `live_digest` attests the current HEAD.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class MemoryGraphStub:
    store_id = "memory:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    def _memory(self):
        st = self.backend.load()
        return st, st.setdefault("memory", {"log": [], "snapshot_seq": 0, "log_offset": 0})

    # ---- functional internals (the actual execution engine) ----------------
    def append(self, op: str, origin: str) -> int:
        st, m = self._memory()
        seq = len(m["log"])
        m["log"].append({"seq": seq, "op": op, "origin": origin})
        m["snapshot_seq"] = m["log_offset"] = len(m["log"])
        self.backend.save(st)
        return seq

    def append_many(self, count: int, origin_fn) -> None:
        """Bulk append (one I/O) of `count` events; origin_fn(i)->origin tag."""
        st, m = self._memory()
        base = len(m["log"])
        for i in range(count):
            m["log"].append({"seq": base + i, "op": "upsert_node", "origin": origin_fn(base + i)})
        m["snapshot_seq"] = m["log_offset"] = len(m["log"])
        self.backend.save(st)

    def set_head(self, snapshot_seq: int, log_offset: int) -> None:
        st, m = self._memory()
        m["snapshot_seq"], m["log_offset"] = snapshot_seq, log_offset
        self.backend.save(st)

    # ---- StateStore interface (unchanged semantics) ------------------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(f"{coordinate['snapshot_seq']}:{coordinate['log_offset']}")

    def restore(self, coordinate: dict) -> str:
        # rewind HEAD (re-materialize) — the log itself is left intact.
        self.set_head(coordinate["snapshot_seq"], coordinate["log_offset"])
        return self.live_digest()

    def live_digest(self) -> str:
        h = self.live_head()
        return digest(f"{h.get('snapshot_seq')}:{h.get('log_offset')}")

    # ---- observability -----------------------------------------------------
    def live_head(self) -> dict:
        m = self.backend.get("memory", {})
        return {"snapshot_seq": m.get("snapshot_seq"), "log_offset": m.get("log_offset")}

    def log_size(self) -> int:
        return len(self.backend.get("memory", {}).get("log", []))

    def tombstoned_after_head(self) -> int:
        """Events that exist in the log but are past HEAD (would replay on roll-forward)."""
        m = self.backend.get("memory", {})
        return max(0, len(m.get("log", [])) - (m.get("log_offset") or 0))
