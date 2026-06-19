"""Qdrant-backed vector StateStore (post-1.0: a second managed vector backend).

Proves the `StateStore` protocol is the only seam - a new vector backend drops into
`rollback.py::_stores()` with no orchestrator change. Qdrant has **native collection aliases**, so
the alias-swap restore maps onto them exactly: each commit-scoped namespace is a Qdrant collection,
and a per-project `live__<project>` alias points at the live one. Restore = atomically re-point that
alias (historical collections are never touched → genuinely restorable). Reuses the shared `digest`
formula verbatim, so a checkpoint sealed against any backend verifies here.

Selected via `AGENTCTL_STATE_BACKEND=qdrant` (+ a reachable Qdrant at `QDRANT_URL`). Needs the
optional `qdrant-client` dep (`pip install 'agentctl[qdrant]'`).
"""
from __future__ import annotations

from agentctl.config import QDRANT_URL, VECTOR_DIM
from agentctl.rollback.stores.base import digest


class QdrantStore:
    store_id = "vector:qdrant"

    def __init__(self, project_id: str, url: str | None = None):
        from qdrant_client import QdrantClient
        self.project_id = project_id
        self._c = QdrantClient(url=url or QDRANT_URL, check_compatibility=False)
        self._alias = f"live__{project_id}"
        self._prefix = f"{project_id}__"          # collection = <project>__<namespace>

    def _col(self, ns: str) -> str:
        return self._prefix + ns

    def _ns(self, col: str) -> str:
        return col[len(self._prefix):] if col.startswith(self._prefix) else col

    # ---- functional internals ------------------------------------------------
    def upsert(self, namespace: str, ids, snapshot_id: str | None = None) -> None:
        from qdrant_client import models
        col = self._col(namespace)
        if not self._c.collection_exists(col):
            self._c.create_collection(
                col, vectors_config=models.VectorParams(size=VECTOR_DIM, distance=models.Distance.COSINE))
        pts = [models.PointStruct(id=int(i), vector=[0.0] * VECTOR_DIM, payload={"vid": str(i)})
               for i in ids]
        if pts:
            self._c.upsert(col, points=pts)

    def set_alias(self, namespace: str) -> None:
        from qdrant_client import models
        col = self._col(namespace)
        ops = []
        if any(a.alias_name == self._alias for a in self._c.get_aliases().aliases):
            ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self._alias)))
        ops.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=col, alias_name=self._alias)))
        self._c.update_collection_aliases(change_aliases_operations=ops)  # atomic swap

    def reset(self) -> None:
        for c in self._c.get_collections().collections:
            if c.name.startswith(self._prefix):
                self._c.delete_collection(c.name)   # also drops aliases pointing at it

    # ---- StateStore interface (identical semantics to the stub) --------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(coordinate["namespace"])

    def restore(self, coordinate: dict) -> str:
        self.set_alias(coordinate["namespace"])     # the alias swap IS the restore (idempotent)
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(self.live_namespace() or "")

    # ---- observability -------------------------------------------------------
    def live_namespace(self) -> str | None:
        for a in self._c.get_aliases().aliases:
            if a.alias_name == self._alias:
                return self._ns(a.collection_name)
        return None

    def live_vector_count(self) -> int:
        ns = self.live_namespace()
        return self._c.count(self._col(ns)).count if ns else 0
