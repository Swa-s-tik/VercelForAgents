"""Real pgvector-backed vector StateStore (Workstream 1).

The exact same contract as VectorStoreStub - commit-scoped collections + an idempotent alias swap
as the restore - but backed by pgvector tables (schema_vector.sql). It reuses the shared ``digest``
formula verbatim, so the ``state_digest`` sealed into a checkpoint matches this store's
``live_digest`` after restore and Vertical C's Phase-3 verification passes unchanged.

Bound to the rollback orchestrator's connection + project, so its writes commit on the same phase
boundaries as the rest of the rollback.
"""
from __future__ import annotations

from agentctl.config import VECTOR_DIM
from agentctl.rollback.stores.base import digest

_ZERO_VEC = "[" + ",".join(["0"] * VECTOR_DIM) + "]"  # demo embedding; digest depends on namespace only


class PgVectorStore:
    store_id = "vector:pgvector"

    def __init__(self, project_id: str, conn):
        self.project_id = project_id
        self.conn = conn

    # ---- functional internals ------------------------------------------------
    def upsert(self, namespace: str, ids, snapshot_id: str | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO vectorstore.embeddings (project_id, collection, vector_id, embedding) "
                "VALUES (%s,%s,%s,%s::public.vector) "
                "ON CONFLICT (project_id, collection, vector_id) "
                "DO UPDATE SET version = vectorstore.embeddings.version + 1",
                [(self.project_id, namespace, str(i), _ZERO_VEC) for i in ids])
            if snapshot_id:
                cur.execute(
                    "INSERT INTO vectorstore.collection_snapshots "
                    "(project_id, collection, snapshot_id, member_ids) "
                    "SELECT %s,%s,%s, coalesce(jsonb_agg(vector_id ORDER BY vector_id),'[]') "
                    "FROM vectorstore.embeddings WHERE project_id=%s AND collection=%s "
                    "ON CONFLICT (project_id, collection, snapshot_id) DO NOTHING",
                    [self.project_id, namespace, snapshot_id, self.project_id, namespace])

    def set_alias(self, namespace: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vectorstore.live_alias (project_id, collection) VALUES (%s,%s) "
                "ON CONFLICT (project_id) DO UPDATE SET collection = EXCLUDED.collection",
                [self.project_id, namespace])

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            for t in ("embeddings", "collection_snapshots", "live_alias"):
                cur.execute(f"DELETE FROM vectorstore.{t} WHERE project_id=%s", [self.project_id])

    # ---- StateStore interface (identical semantics to the stub) --------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(coordinate["namespace"])

    def restore(self, coordinate: dict) -> str:
        self.set_alias(coordinate["namespace"])  # the alias swap IS the restore (idempotent)
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(self.live_namespace() or "")

    # ---- observability -------------------------------------------------------
    def live_namespace(self) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT collection FROM vectorstore.live_alias WHERE project_id=%s",
                        [self.project_id])
            row = cur.fetchone()
        return row["collection"] if row else None

    def live_vector_count(self) -> int:
        ns = self.live_namespace()
        if not ns:
            return 0
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM vectorstore.embeddings "
                        "WHERE project_id=%s AND collection=%s", [self.project_id, ns])
            return cur.fetchone()["n"]
