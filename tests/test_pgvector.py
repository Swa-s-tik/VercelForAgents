"""Real pgvector / Postgres state stores (Workstream 1).

Proves (a) the default backend is still the offline stubs (so the rest of the suite is unaffected),
(b) the pgvector + memory adapters honor the exact StateStore digest contract, and (c) a full
rollback through the pgvector backend restores the alias + memory HEAD and verifies digests, with
the event log left intact (tombstoned past HEAD).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.config as cfg
import agentctl.rollback.rollback as rbmod
import agentctl.rollback as _rb
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.rollback.stores.base import digest
from agentctl.rollback.stores.memory_pg import PgMemoryStore
from agentctl.rollback.stores.vector_pg import PgVectorStore
from agentctl.rollback.stores.vector_stub import VectorStoreStub

_PG = str(Path(_rb.__file__).with_name("schema_postgres.sql"))
_VEC = str(Path(_rb.__file__).parent / "stores" / "schema_vector.sql")


def setup_module(module=None):
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'")
        if cur.fetchone() is None:
            conn.close()
            pytest.skip("pgvector not available (use the pgvector/pgvector:pg16 image)",
                        allow_module_level=True)
    apply_schema(conn, _PG)
    apply_schema(conn, _VEC)
    conn.close()


def test_default_backend_is_stub():
    # env unset (default 'json') -> the offline stubs, so the rest of the suite is untouched.
    assert isinstance(rbmod._stores()["vector_store"], VectorStoreStub)


def test_digest_parity_with_stub():
    # the pg adapters MUST reuse the exact digest formula so sealed state_digests match.
    conn = connect()
    try:
        v = PgVectorStore(DEMO_PROJECT_ID, conn)
        m = PgMemoryStore(DEMO_PROJECT_ID, conn)
        assert v.snapshot({"namespace": "ns-x"}) == digest("ns-x")
        assert m.snapshot({"snapshot_seq": 5, "log_offset": 7}) == digest("5:7")
    finally:
        conn.close()


def test_vector_store_alias_swap_is_idempotent_restore():
    conn = connect()
    try:
        v = PgVectorStore(DEMO_PROJECT_ID, conn)
        v.reset()
        v.upsert("ns-a", range(10))
        v.upsert("ns-b", range(20))
        # restore drives the alias; idempotent; historical collection untouched.
        d1 = v.restore({"namespace": "ns-a"})
        d2 = v.restore({"namespace": "ns-a"})
        assert d1 == d2 == digest("ns-a") == v.live_digest()
        assert v.live_namespace() == "ns-a" and v.live_vector_count() == 10
        # the other collection is intact (genuinely restorable)
        v.set_alias("ns-b")
        assert v.live_vector_count() == 20
        conn.commit()
    finally:
        conn.close()


def test_memory_head_rewind_keeps_log():
    conn = connect()
    try:
        m = PgMemoryStore(DEMO_PROJECT_ID, conn)
        m.reset()
        m.append_many(100, lambda i: "x")
        m.set_head(100, 100)
        assert m.live_digest() == digest("100:100") and m.log_size() == 100
        # rewind HEAD; the log is left intact (tombstoned past HEAD).
        assert m.restore({"snapshot_seq": 60, "log_offset": 60}) == digest("60:60")
        assert m.live_head() == {"snapshot_seq": 60, "log_offset": 60}
        assert m.log_size() == 100 and m.tombstoned_after_head() == 40
        conn.commit()
    finally:
        conn.close()


def test_full_pgvector_rollback(monkeypatch):
    from agentctl.rollback.rollback import rollback_to_commit
    from agentctl.rollback.seed import seed
    monkeypatch.setattr(cfg, "STATE_BACKEND", "pgvector")
    monkeypatch.setattr(rbmod, "STATE_BACKEND", "pgvector")
    conn = connect()
    try:
        info = seed(conn)
        v, m = PgVectorStore(DEMO_PROJECT_ID, conn), PgMemoryStore(DEMO_PROJECT_ID, conn)
        assert v.live_namespace() == "proj-a1-ns-v37" and v.live_vector_count() == 80
        assert m.live_head()["log_offset"] == 1180

        res = rollback_to_commit(conn, info["project_id"], "aaaa1111aaaa", actor="test")
        assert res["status"] == "compensating"          # schema forward-fix + Stripe are irreversible
        # reversible state restored to A's coordinates; no digest-drift entry for vector/memory
        assert v.live_namespace() == "proj-a1-ns-v36" and v.live_vector_count() == 50
        assert m.live_head() == {"snapshot_seq": 1000, "log_offset": 1000}
        assert m.log_size() == 1180 and m.tombstoned_after_head() == 180  # log intact
        classes = {u["class"] for u in res["unrollbackable"]}
        assert classes == {"relational_schema", "side_effect"}
        assert not any("digest drift" in u["reason"] for u in res["unrollbackable"])
    finally:
        conn.close()
