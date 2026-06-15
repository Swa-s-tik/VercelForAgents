"""Seed the rollback demo: two deployments (A, B), each with a sealed checkpoint manifest,
with B currently 100% live. External-store stub state is initialized to B's coordinates.

Scenario:
  A  sha=aaaa1111aaaa  schema v36  vector ns=...v36  memory HEAD 1000  (the rollback target)
  B  sha=bbbb2222bbbb  schema v37  vector ns=...v37  memory HEAD 1180  + a Stripe charge
Rolling back to A: vector alias + memory HEAD are restored (reversible); the schema down
(37->36) is refused as forward-fix; B's Stripe charge is irreversible -> compensated.
"""
from __future__ import annotations

import psycopg
from psycopg.types.json import Json

from agentctl.config import DEMO_PROJECT_ID
from agentctl.rollback import manifest as mf, routing
from agentctl.rollback.models import Pointer
from agentctl.rollback.stores.base import JsonBackend
from agentctl.rollback.stores.memory_stub import MemoryGraphStub
from agentctl.rollback.stores.schema_stub import SchemaStoreStub
from agentctl.rollback.stores.vector_stub import VectorStoreStub

SHA_A = "aaaa1111aaaa"
SHA_B = "bbbb2222bbbb"

_vec, _mem, _sch = VectorStoreStub(), MemoryGraphStub(), SchemaStoreStub()


def _pointers(ns: str, migration: int, seq: int, with_side_effect: bool) -> list[Pointer]:
    pts = [
        Pointer("vector_store", "reversible", "vector:demo",
                {"namespace": ns, "snapshot_id": f"snap-{ns}"},
                state_digest=_vec.snapshot({"namespace": ns}), strategy="namespace_swap"),
        Pointer("relational_schema", "forward_fix", "appdb:demo",
                {"migration_version": migration, "expand_contract_phase": "contract",
                 "down_sql_ref": f"mig/{migration:04d}_down.sql"},
                state_digest=_sch.snapshot({"migration_version": migration}), strategy="expand_contract"),
        Pointer("memory_graph", "reversible", "memory:demo",
                {"graph_id": "user:8841", "snapshot_seq": seq, "log_offset": seq},
                state_digest=_mem.snapshot({"snapshot_seq": seq, "log_offset": seq}),
                strategy="event_sourced_rewind"),
    ]
    if with_side_effect:
        pts.append(Pointer("side_effect", "irreversible", "stripe",
                           {"provider": "stripe", "idempotency_key": "pay_bbbb2222_001",
                            "external_ref": "ch_3PXyz", "compensation": "refund"},
                           strategy="compensate_or_flag"))
    return pts


def _insert_deployment(conn, project_id, sha, migration) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO controlplane.deployments
               (project_id, git_commit_sha, status, created_by, build_meta)
               VALUES (%s,%s,'ready','seed',%s) RETURNING id""",
            [project_id, sha, Json({"migration_version": migration})])
        return cur.fetchone()["id"]


def seed(conn: psycopg.Connection, project_id: str = DEMO_PROJECT_ID) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE controlplane.deployments, controlplane.routing_tables, "
            "controlplane.checkpoints, controlplane.rollbacks, controlplane.audit_log, "
            "controlplane.memory_sync_pointers, controlplane.otel_spans "
            "RESTART IDENTITY CASCADE")
    conn.commit()

    a_id = _insert_deployment(conn, project_id, SHA_A, 36)
    b_id = _insert_deployment(conn, project_id, SHA_B, 37)
    conn.commit()

    mf.seal_checkpoint(conn, a_id, SHA_A, _pointers("proj-a1-ns-v36", 36, 1000, with_side_effect=False))
    mf.seal_checkpoint(conn, b_id, SHA_B, _pointers("proj-a1-ns-v37", 37, 1180, with_side_effect=True))

    # memory-sync pointers (table populated for completeness)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO controlplane.memory_sync_pointers
                       (project_id, graph_id, deployment_id, snapshot_seq, log_offset, digest)
                       VALUES (%s,'user:8841',%s,1000,1000,%s), (%s,'user:8841',%s,1180,1180,%s)""",
                    [project_id, a_id, _mem.snapshot({"snapshot_seq": 1000, "log_offset": 1000}),
                     project_id, b_id, _mem.snapshot({"snapshot_seq": 1180, "log_offset": 1180})])
    conn.commit()

    # B goes 100% live (deploy).
    routing.flip_routing(conn, project_id, b_id, reason=f"deploy:{SHA_B}", actor="seed", notify=False)

    # External-store stub live state == B's coordinates.
    JsonBackend().save({
        "vector": {"alias_namespace": "proj-a1-ns-v37"},
        "memory": {"snapshot_seq": 1180, "log_offset": 1180},
        "schema": {"migration_version": 37},
    })

    return {"project_id": project_id, "A": {"id": a_id, "sha": SHA_A}, "B": {"id": b_id, "sha": SHA_B}}
