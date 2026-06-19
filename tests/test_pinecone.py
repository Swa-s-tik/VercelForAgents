"""Pinecone vector StateStore (post-1.0, the third managed backend).

Pinecone has no aliases, so the adapter models the alias-swap with a pointer record. The constructor
takes an injected index, so the StateStore semantics (alias-swap restore, digest parity, historical
namespaces preserved) are verified here against an in-memory FakeIndex - no account needed. A live
integration test self-skips unless `pinecone` is installed and reachable.
"""
from __future__ import annotations

import pytest

from agentctl.config import DEMO_PROJECT_ID, VECTOR_DIM
from agentctl.rollback.stores.base import digest
from agentctl.rollback.stores.pinecone_store import PineconeStore


class FakeIndex:
    """Minimal in-memory stand-in for a pinecone Index: namespace -> {id -> {values, metadata}}."""
    def __init__(self):
        self.ns: dict[str, dict] = {}

    def upsert(self, vectors, namespace):
        bucket = self.ns.setdefault(namespace, {})
        for v in vectors:
            bucket[v["id"]] = {"values": v["values"], "metadata": v.get("metadata", {})}

    def fetch(self, ids, namespace):
        bucket = self.ns.get(namespace, {})
        return {"vectors": {i: bucket[i] for i in ids if i in bucket}}

    def delete(self, delete_all, namespace):
        if delete_all:
            self.ns.pop(namespace, None)

    def describe_index_stats(self):
        return {"namespaces": {n: {"vector_count": len(v)} for n, v in self.ns.items()}}


def _store():
    return PineconeStore(DEMO_PROJECT_ID, index=FakeIndex())


def test_digest_parity_with_stub():
    assert _store().snapshot({"namespace": "ns-x"}) == digest("ns-x")


def test_alias_swap_is_idempotent_restore():
    v = _store()
    v.upsert("ns-a", range(10))
    v.upsert("ns-b", range(20))

    d1 = v.restore({"namespace": "ns-a"})
    d2 = v.restore({"namespace": "ns-a"})          # idempotent
    assert d1 == d2 == digest("ns-a") == v.live_digest()
    assert v.live_namespace() == "ns-a" and v.live_vector_count() == 10

    v.set_alias("ns-b")                            # the other namespace is intact (restorable)
    assert v.live_namespace() == "ns-b" and v.live_vector_count() == 20
    v.set_alias("ns-a")                            # swap back -> historical state still there
    assert v.live_vector_count() == 10


def test_live_namespace_none_before_any_restore():
    assert _store().live_namespace() is None
    assert _store().live_digest() == digest("")


def test_pointer_isolated_from_vectors_and_dim():
    v = _store()
    v.upsert("ns-a", range(3))
    v.set_alias("ns-a")
    # the live pointer lives in its own namespace; vector dim matches config
    assert len(v._index.ns[v._store_ns("ns-a")]) == 3
    assert all(len(rec["values"]) == VECTOR_DIM
               for rec in v._index.ns[v._store_ns("ns-a")].values())


# ---- live integration (self-skips without pinecone + a reachable index) ---------------------- #
def _pinecone_up() -> bool:
    try:
        from agentctl.config import PINECONE_API_KEY, PINECONE_INDEX
        if not PINECONE_API_KEY:
            return False
        from pinecone import Pinecone
        Pinecone(api_key=PINECONE_API_KEY).Index(PINECONE_INDEX).describe_index_stats()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pinecone_up(), reason="no reachable Pinecone (PINECONE_API_KEY/INDEX)")
def test_live_pinecone_restore():
    v = PineconeStore(DEMO_PROJECT_ID)
    v.reset()
    v.upsert("ns-a", range(5))
    v.upsert("ns-b", range(7))
    assert v.restore({"namespace": "ns-a"}) == digest("ns-a") == v.live_digest()
    assert v.live_namespace() == "ns-a"
    v.reset()
