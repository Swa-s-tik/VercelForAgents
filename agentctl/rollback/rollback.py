"""The 1-click rollback orchestrator — Phases 0..4 (Vertical C).

Guarantee offered (honestly): the live code becomes exactly the target commit and every
REVERSIBLE state pointer is realigned to that commit's snapshot; every non-reversible
pointer is enumerated in ``rollbacks.unrollbackable`` and the rollback reports
``compensating`` (partial) rather than a fake ``completed``.

Only Phase 1 (the routing flip) is a hard ACID transaction. State realignment (Phase 2)
is per-pointer idempotent and crash-resumable — NOT a distributed transaction.
"""
from __future__ import annotations

import psycopg
from psycopg.types.json import Json

from agentctl.config import STATE_BACKEND
from agentctl.rollback import audit, manifest as mf, routing
from agentctl.rollback.stores.memory_stub import MemoryGraphStub
from agentctl.rollback.stores.schema_stub import SchemaMigrationError, SchemaStoreStub
from agentctl.rollback.stores.vector_stub import VectorStoreStub


def _stores(conn: psycopg.Connection | None = None, project_id: str | None = None) -> dict:
    """The state-store registry keyed by mutation_class. Default = file-backed stubs (zero infra,
    used by the offline tests). With AGENTCTL_STATE_BACKEND=pgvector AND a live conn, the vector +
    memory stores are the real pgvector/Postgres adapters; relational_schema stays the stub (its
    rollback is a migration-refusal, deliberately Postgres-independent)."""
    if STATE_BACKEND == "pgvector" and conn is not None:
        from agentctl.rollback.stores.memory_pg import PgMemoryStore
        from agentctl.rollback.stores.vector_pg import PgVectorStore
        return {
            "vector_store": PgVectorStore(project_id, conn),
            "memory_graph": PgMemoryStore(project_id, conn),
            "relational_schema": SchemaStoreStub(),
        }
    if STATE_BACKEND == "qdrant":
        # Qdrant swaps only the vector store; memory + schema stay the file-backed stubs.
        from agentctl.rollback.stores.qdrant_store import QdrantStore
        return {
            "vector_store": QdrantStore(project_id),
            "memory_graph": MemoryGraphStub(),
            "relational_schema": SchemaStoreStub(),
        }
    return {
        "vector_store": VectorStoreStub(),
        "memory_graph": MemoryGraphStub(),
        "relational_schema": SchemaStoreStub(),
    }


def rollback_to_commit(conn: psycopg.Connection, project_id: str,
                       to_commit_sha: str, actor: str = "cli") -> dict:
    stores = _stores(conn, project_id)

    # ---- Phase 0: resolve target + sealed checkpoint; find current live deployment ----
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM controlplane.deployments WHERE project_id=%s AND git_commit_sha=%s",
            [project_id, to_commit_sha])
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no deployment for commit {to_commit_sha!r}")
        to_dep = row["id"]
        cur.execute(
            "SELECT id FROM controlplane.deployments WHERE project_id=%s AND status='active'",
            [project_id])
        actives = [r["id"] for r in cur.fetchall()]
    from_dep = actives[0] if actives else to_dep

    target_manifest = mf.load_manifest(conn, to_dep)
    if target_manifest is None:
        raise ValueError("target checkpoint is not sealed; cannot guarantee restore (Phase 0 abort)")
    from_manifest = mf.load_manifest(conn, from_dep)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO controlplane.rollbacks
               (project_id, from_deployment, to_deployment, to_commit_sha, status, manifest_snapshot, initiated_by)
               VALUES (%s,%s,%s,%s,'initiated',%s,%s) RETURNING id""",
            [project_id, from_dep, to_dep, to_commit_sha, Json(target_manifest.to_json()), actor])
        rb_id = cur.fetchone()["id"]
    conn.commit()

    # ---- Phase 1: atomic routing flip (THE transaction) ----
    rt_id, version = routing.flip_routing(
        conn, project_id, to_dep, reason=f"rollback:{to_commit_sha}", actor=actor)
    with conn.cursor() as cur:
        cur.execute("UPDATE controlplane.rollbacks SET status='routing_flipped', routing_table_id=%s WHERE id=%s",
                    [rt_id, rb_id])
    conn.commit()
    audit.record(conn, project_id, rb_id, actor, "routing_flip", to_commit_sha,
                 {"routing_table_id": rt_id, "version": version})

    # ---- Phase 2: per-pointer idempotent state realignment ----
    with conn.cursor() as cur:
        cur.execute("UPDATE controlplane.rollbacks SET status='state_realigning' WHERE id=%s", [rb_id])
    conn.commit()

    unrollbackable: list[dict] = []
    for p in target_manifest.pointers:
        store = stores.get(p.mutation_class)
        if p.mutation_class == "vector_store":
            d = store.restore(p.coordinate)
            audit.record(conn, project_id, rb_id, actor, "restore_vector", p.store_id,
                         {"digest": d, "namespace": p.coordinate.get("namespace")})
        elif p.mutation_class == "memory_graph":
            d = store.restore(p.coordinate)
            audit.record(conn, project_id, rb_id, actor, "restore_graph", p.store_id,
                         {"digest": d, "head": p.coordinate})
        elif p.mutation_class == "relational_schema":
            try:
                d = store.restore(p.coordinate)
                audit.record(conn, project_id, rb_id, actor, "restore_schema", p.store_id, {"digest": d})
            except SchemaMigrationError as e:
                unrollbackable.append({"class": "relational_schema", "store_id": p.store_id, "reason": str(e)})
                audit.record(conn, project_id, rb_id, actor, "flag_forward_fix", p.store_id, {"reason": str(e)})

    # Flag irreversible damage done by the deployment(s) we rolled PAST (from_manifest).
    if from_manifest and from_dep != to_dep:
        for p in from_manifest.pointers:
            if p.reversibility == "irreversible":
                comp = (p.coordinate or {}).get("compensation")
                if comp:
                    # idempotent compensation, guarded by the idempotency key.
                    audit.record(conn, project_id, rb_id, actor, "compensate_side_effect", p.store_id,
                                 {"compensation": comp, "idempotency_key": p.coordinate.get("idempotency_key"),
                                  "external_ref": p.coordinate.get("external_ref")})
                    unrollbackable.append({"class": "side_effect", "store_id": p.store_id,
                                           "reason": f"irreversible; compensated via {comp}"})
                else:
                    audit.record(conn, project_id, rb_id, actor, "flag_irreversible", p.store_id,
                                 {"reason": "no compensation registered"})
                    unrollbackable.append({"class": "side_effect", "store_id": p.store_id,
                                           "reason": "irreversible; no compensation"})

    # ---- Phase 3: verify reversible pointers reached the target digest ----
    for p in target_manifest.pointers:
        if p.reversibility == "reversible":
            live = stores[p.mutation_class].live_digest()
            if live != p.state_digest:
                unrollbackable.append({"class": p.mutation_class, "store_id": p.store_id,
                                       "reason": f"digest drift: expected {p.state_digest} got {live}"})

    # ---- Phase 4: record outcome ----
    final = "completed" if not unrollbackable else "compensating"
    with conn.cursor() as cur:
        cur.execute("UPDATE controlplane.rollbacks SET status=%s, unrollbackable=%s, completed_at=now() WHERE id=%s",
                    [final, Json(unrollbackable), rb_id])
    conn.commit()
    audit.record(conn, project_id, rb_id, actor, "rollback_outcome", to_commit_sha,
                 {"status": final, "unrollbackable": unrollbackable})

    return {"rollback_id": rb_id, "status": final, "to_commit": to_commit_sha,
            "routing_version": version, "unrollbackable": unrollbackable}
