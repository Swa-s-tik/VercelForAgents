"""Pinecone-backed vector StateStore (post-1.0: the third managed vector backend).

Like the Qdrant adapter, this proves the `StateStore` protocol is the only seam - it drops into
`rollback.py::_stores()` with no orchestrator change and reuses the shared `digest` formula verbatim.

Pinecone has no native collection aliases (unlike Qdrant), so the alias-swap restore is modelled with
a single **pointer record**: each commit-scoped namespace holds that snapshot's vectors, and one record
(`id="pointer"`) in a per-project `__live__<project>` namespace carries `metadata={"namespace": <live>}`.
Restore = upsert that pointer to the target namespace (idempotent); historical namespaces are never
deleted, so past state is genuinely restorable. The metadata read is the live digest's input, so the
digest contract is identical to every other backend.

Selected via `AGENTCTL_STATE_BACKEND=pinecone` (+ `PINECONE_API_KEY` / `PINECONE_INDEX`). Needs the
optional `pinecone` dep (`pip install 'agentctl[pinecone]'`). The constructor accepts an injected
``index`` so the alias-swap semantics are unit-tested with an in-memory fake (no account required); the
live integration test self-skips without a reachable Pinecone.
"""
from __future__ import annotations

from agentctl.config import PINECONE_API_KEY, PINECONE_INDEX, VECTOR_DIM
from agentctl.rollback.stores.base import digest

_POINTER_ID = "pointer"


def _zero_vec() -> list[float]:
    return [0.0] * VECTOR_DIM


def _meta(vec) -> dict:
    """Extract a vector record's metadata across pinecone-client response shapes (attr or dict)."""
    if vec is None:
        return {}
    m = getattr(vec, "metadata", None)
    if m is None and isinstance(vec, dict):
        m = vec.get("metadata")
    return m or {}


def _ns_count(stats, ns: str) -> int:
    """Vector count for a namespace from describe_index_stats(), across response shapes."""
    spaces = getattr(stats, "namespaces", None)
    if spaces is None and isinstance(stats, dict):
        spaces = stats.get("namespaces")
    info = (spaces or {}).get(ns)
    if info is None:
        return 0
    c = getattr(info, "vector_count", None)
    if c is None and isinstance(info, dict):
        c = info.get("vector_count")
    return int(c or 0)


class PineconeStore:
    store_id = "vector:pinecone"

    def __init__(self, project_id: str, *, index=None, api_key: str | None = None,
                 index_name: str | None = None):
        self.project_id = project_id
        self._live_ns = f"__live__{project_id}"
        self._prefix = f"{project_id}__"            # storage namespace = <project>__<namespace>
        if index is not None:
            self._index = index                     # injected (tests / custom wiring)
        else:  # pragma: no cover - needs the real client + an account
            from pinecone import Pinecone
            pc = Pinecone(api_key=api_key or PINECONE_API_KEY)
            self._index = pc.Index(index_name or PINECONE_INDEX)

    def _store_ns(self, namespace: str) -> str:
        return self._prefix + namespace

    # ---- functional internals ------------------------------------------------
    def upsert(self, namespace: str, ids, snapshot_id: str | None = None) -> None:
        vectors = [{"id": str(i), "values": _zero_vec(), "metadata": {"vid": str(i)}} for i in ids]
        if vectors:
            self._index.upsert(vectors=vectors, namespace=self._store_ns(namespace))

    def set_alias(self, namespace: str) -> None:
        """Re-point the live pointer at ``namespace`` (the alias swap = restore). Idempotent."""
        self._index.upsert(
            vectors=[{"id": _POINTER_ID, "values": _zero_vec(), "metadata": {"namespace": namespace}}],
            namespace=self._live_ns)

    def reset(self) -> None:
        # drop everything for this project (the live pointer + every commit-scoped namespace)
        try:
            stats = self._index.describe_index_stats()
            spaces = getattr(stats, "namespaces", None) or (
                stats.get("namespaces") if isinstance(stats, dict) else {}) or {}
            for ns in list(spaces):
                if ns == self._live_ns or ns.startswith(self._prefix):
                    self._index.delete(delete_all=True, namespace=ns)
        except Exception:
            pass

    # ---- StateStore interface (identical semantics to the stub) --------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(coordinate["namespace"])

    def restore(self, coordinate: dict) -> str:
        self.set_alias(coordinate["namespace"])     # idempotent pointer swap
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(self.live_namespace() or "")

    # ---- observability -------------------------------------------------------
    def live_namespace(self) -> str | None:
        r = self._index.fetch(ids=[_POINTER_ID], namespace=self._live_ns)
        vecs = getattr(r, "vectors", None)
        if vecs is None and isinstance(r, dict):
            vecs = r.get("vectors")
        ns = _meta((vecs or {}).get(_POINTER_ID)).get("namespace")
        return ns or None

    def live_vector_count(self) -> int:
        ns = self.live_namespace()
        if not ns:
            return 0
        return _ns_count(self._index.describe_index_stats(), self._store_ns(ns))
