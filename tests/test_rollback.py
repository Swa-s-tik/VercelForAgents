"""Integration test for the stateful rollback engine (needs Postgres-in-Docker).
Runs under pytest OR as a plain script (`python tests/test_rollback.py`)."""
from __future__ import annotations

from pathlib import Path

import agentctl.rollback as rbpkg
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.rollback import audit
from agentctl.rollback.rollback import rollback_to_commit
from agentctl.rollback.routing import live_routing
from agentctl.rollback.seed import SHA_A, SHA_B, seed
from agentctl.rollback.stores.memory_stub import MemoryGraphStub
from agentctl.rollback.stores.schema_stub import SchemaStoreStub
from agentctl.rollback.stores.vector_stub import VectorStoreStub

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))


def setup_module(module=None):
    conn = connect()
    apply_schema(conn, _SCHEMA)
    conn.close()


def test_full_rollback():
    conn = connect()
    seed(conn)

    # --- B is 100% live; external state at B's coordinates ---
    rows = live_routing(conn, DEMO_PROJECT_ID)
    assert len(rows) == 1 and rows[0]["git_commit_sha"] == SHA_B and rows[0]["weight"] == 10000
    assert VectorStoreStub().live_namespace() == "proj-a1-ns-v37"
    assert MemoryGraphStub().live_head()["snapshot_seq"] == 1180
    assert SchemaStoreStub().live_version() == 37

    # --- 1-click rollback to A ---
    res = rollback_to_commit(conn, DEMO_PROJECT_ID, SHA_A)
    assert res["status"] == "compensating", res   # partial: schema forward-fix + stripe

    # routing flipped atomically to A, exactly one live table (partial-unique invariant)
    rows = live_routing(conn, DEMO_PROJECT_ID)
    assert len(rows) == 1 and rows[0]["git_commit_sha"] == SHA_A and rows[0]["weight"] == 10000
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM controlplane.routing_tables WHERE project_id=%s AND is_live",
                    [DEMO_PROJECT_ID])
        assert cur.fetchone()["c"] == 1

    # reversible state restored to A; schema NOT auto-downgraded (forward-fix)
    assert VectorStoreStub().live_namespace() == "proj-a1-ns-v36"
    assert MemoryGraphStub().live_head()["snapshot_seq"] == 1000
    assert SchemaStoreStub().live_version() == 37

    # honesty: non-reversible items are enumerated, not hidden
    reasons = " ".join(u["reason"] for u in res["unrollbackable"])
    assert "forward-fix" in reasons
    assert "compensated via refund" in reasons

    # audit trail ordering
    actions = [r["action"] for r in audit.fetch(conn, res["rollback_id"])]
    assert actions[0] == "routing_flip"
    assert {"restore_vector", "restore_graph", "flag_forward_fix", "compensate_side_effect"} <= set(actions)
    assert actions[-1] == "rollback_outcome"
    conn.close()


if __name__ == "__main__":
    setup_module()
    test_full_rollback()
    print("rollback integration test passed")
