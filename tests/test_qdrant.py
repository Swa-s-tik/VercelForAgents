"""Qdrant vector StateStore (post-1.0). Skips unless qdrant-client is installed and a Qdrant is
reachable at QDRANT_URL — so the default suite is unaffected."""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.config as cfg
import agentctl.rollback as _rb
import agentctl.rollback.rollback as rbmod
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID, QDRANT_URL
from agentctl.rollback.stores.base import digest

_PG = str(Path(_rb.__file__).with_name("schema_postgres.sql"))

qdrant_client = pytest.importorskip("qdrant_client", reason="qdrant-client not installed")


def _qdrant_up() -> bool:
    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=QDRANT_URL, check_compatibility=False).get_collections()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_up(),
                                reason=f"no Qdrant reachable at {QDRANT_URL}")


def setup_module(module=None):
    conn = connect()
    apply_schema(conn, _PG)
    conn.close()


def _store():
    from agentctl.rollback.stores.qdrant_store import QdrantStore
    return QdrantStore(DEMO_PROJECT_ID)


def test_digest_parity_with_stub():
    v = _store()
    assert v.snapshot({"namespace": "ns-x"}) == digest("ns-x")


def test_alias_swap_is_idempotent_restore():
    v = _store()
    v.reset()
    v.upsert("ns-a", range(10))
    v.upsert("ns-b", range(20))
    d1 = v.restore({"namespace": "ns-a"})
    d2 = v.restore({"namespace": "ns-a"})          # idempotent
    assert d1 == d2 == digest("ns-a") == v.live_digest()
    assert v.live_namespace() == "ns-a" and v.live_vector_count() == 10
    v.set_alias("ns-b")                            # the other collection is intact
    assert v.live_vector_count() == 20
    v.reset()


def test_full_qdrant_rollback(monkeypatch):
    from agentctl.rollback.rollback import rollback_to_commit
    from agentctl.rollback.seed import seed
    monkeypatch.setattr(cfg, "STATE_BACKEND", "qdrant")
    monkeypatch.setattr(rbmod, "STATE_BACKEND", "qdrant")
    conn = connect()
    try:
        info = seed(conn)
        v = _store()
        assert v.live_namespace() == "proj-a1-ns-v37" and v.live_vector_count() == 80

        res = rollback_to_commit(conn, info["project_id"], "aaaa1111aaaa", actor="test")
        assert res["status"] == "compensating"
        assert v.live_namespace() == "proj-a1-ns-v36" and v.live_vector_count() == 50
        # both commit-scoped collections survive (historical state genuinely restorable)
        names = {c.name.split("__")[-1] for c in v._c.get_collections().collections}
        assert {"proj-a1-ns-v36", "proj-a1-ns-v37"} <= names
        # vector restore verified (no digest-drift entry); only schema + side_effect are unrollbackable
        assert {u["class"] for u in res["unrollbackable"]} == {"relational_schema", "side_effect"}
    finally:
        conn.close()
