"""Append-only audit trail (Vertical C). Each call is its own short commit, so audit
rows survive even if a later realignment step fails. Never call inside an open
``conn.transaction()`` block (it commits)."""
from __future__ import annotations

import psycopg
from psycopg.types.json import Json


def record(conn: psycopg.Connection, project_id: str, rollback_id: int | None,
           actor: str, action: str, target_ref: str | None = None,
           payload: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO controlplane.audit_log
               (project_id, rollback_id, actor, action, target_ref, payload)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            [project_id, rollback_id, actor, action, target_ref, Json(payload or {})])
    conn.commit()


def fetch(conn: psycopg.Connection, rollback_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT action, target_ref, payload, occurred_at
               FROM controlplane.audit_log WHERE rollback_id=%s ORDER BY occurred_at, id""",
            [rollback_id])
        return cur.fetchall()
