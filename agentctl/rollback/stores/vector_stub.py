"""Vector store - functional mock of commit-scoped collections + alias swap (Phase 8).

Models the real strategy concretely: each deployment writes into its own commit-scoped
collection (namespace); versioned upserts bump a per-vector version; an immutable snapshot
can be frozen per collection; the project's LIVE alias points at one collection. Rollback is
an O(1), idempotent ALIAS SWAP to the target commit's collection - the previous collection is
never mutated (so it is genuinely restorable). `live_digest` attests which collection is live.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class VectorStoreStub:
    store_id = "vector:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    # ---- functional internals (the actual execution engine) ----------------
    def _vector(self) -> dict:
        st = self.backend.load()
        return st, st.setdefault("vector", {"collections": {}, "alias_namespace": None})

    def upsert(self, namespace: str, ids, snapshot_id: str | None = None) -> None:
        """Versioned upsert into a commit-scoped collection (creates it if absent)."""
        st, v = self._vector()
        col = v.setdefault("collections", {}).setdefault(namespace, {"vectors": {}, "snapshots": {}})
        for i in ids:
            k = str(i)
            col["vectors"][k] = col["vectors"].get(k, 0) + 1   # bump version
        if snapshot_id:
            col["snapshots"][snapshot_id] = sorted(col["vectors"])
        self.backend.save(st)

    def set_alias(self, namespace: str) -> None:
        st, v = self._vector()
        v["alias_namespace"] = namespace
        self.backend.save(st)

    # ---- StateStore interface (unchanged semantics) ------------------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(coordinate["namespace"])

    def restore(self, coordinate: dict) -> str:
        # the alias swap IS the restore - idempotent, leaves both collections intact.
        self.set_alias(coordinate["namespace"])
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(self.live_namespace() or "")

    # ---- observability -----------------------------------------------------
    def live_namespace(self) -> str | None:
        return self.backend.get("vector", {}).get("alias_namespace")

    def collections(self) -> dict:
        return self.backend.get("vector", {}).get("collections", {})

    def live_vector_count(self) -> int:
        cols = self.collections()
        ns = self.live_namespace()
        return len(cols.get(ns, {}).get("vectors", {})) if ns else 0
