"""Build / seal / load checkpoint manifests against Postgres (Vertical C)."""
from __future__ import annotations

import psycopg
from psycopg.types.json import Json

from agentctl.rollback.models import Manifest, Pointer


def seal_checkpoint(conn: psycopg.Connection, deployment_id: int,
                    git_commit_sha: str, pointers: list[Pointer]) -> int:
    """Persist a sealed (immutable, restorable) checkpoint + its normalized state_pointers."""
    manifest = Manifest(git_commit_sha, deployment_id, pointers)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO controlplane.checkpoints
               (deployment_id, git_commit_sha, status, manifest, sealed_at)
               VALUES (%s,%s,'sealed',%s, now()) RETURNING id""",
            [deployment_id, git_commit_sha, Json(manifest.to_json())])
        cp_id = cur.fetchone()["id"]
        for p in pointers:
            cur.execute(
                """INSERT INTO controlplane.state_pointers
                   (checkpoint_id, mutation_class, reversibility, store_id, coordinate, state_digest)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                [cp_id, p.mutation_class, p.reversibility, p.store_id,
                 Json(p.coordinate), p.state_digest])
    conn.commit()
    return cp_id


def load_manifest(conn: psycopg.Connection, deployment_id: int) -> Manifest | None:
    """Load a SEALED checkpoint's manifest. Returns None if absent or not sealed
    (Phase 0 rejects unsealed checkpoints - we cannot guarantee restore)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, git_commit_sha, status FROM controlplane.checkpoints WHERE deployment_id=%s",
            [deployment_id])
        cp = cur.fetchone()
        if not cp or cp["status"] != "sealed":
            return None
        cur.execute(
            """SELECT mutation_class, reversibility, store_id, coordinate, state_digest
               FROM controlplane.state_pointers WHERE checkpoint_id=%s ORDER BY id""",
            [cp["id"]])
        pointers = [
            Pointer(r["mutation_class"], r["reversibility"], r["store_id"],
                    r["coordinate"], r["state_digest"])
            for r in cur.fetchall()
        ]
    return Manifest(cp["git_commit_sha"], deployment_id, pointers)
