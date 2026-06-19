"""Read models for the dashboard - thin SQL over the controlplane system-of-record. Every function
returns plain dicts/lists (psycopg is opened with dict rows), so render.py and the tests never touch
a live connection. Read-only; the only write path is rollback_to_commit, called from app.py."""
from __future__ import annotations

import psycopg


def list_deployments(conn: psycopg.Connection, project_id: str) -> list[dict]:
    """Every deployment with its weight in the LIVE routing table (0 if absent) + canary/shadow flags.
    Newest first. `in_live_table` distinguishes 'serving 0%' from 'not in the live table at all'."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.git_commit_sha, d.status::text AS status, d.created_by, d.created_at,
                   COALESCE(rr.weight, 0)            AS weight,
                   COALESCE(rr.is_canary, false)     AS is_canary,
                   COALESCE(rr.shadow_target, false) AS shadow_target,
                   (rr.id IS NOT NULL)               AS in_live_table
            FROM controlplane.deployments d
            LEFT JOIN controlplane.routing_tables rt
                   ON rt.project_id = d.project_id AND rt.is_live
            LEFT JOIN controlplane.routing_rules rr
                   ON rr.routing_table_id = rt.id AND rr.deployment_id = d.id
            WHERE d.project_id = %s
            ORDER BY d.id DESC
            """,
            [project_id],
        )
        return cur.fetchall()


def live_routing_version(conn: psycopg.Connection, project_id: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM controlplane.routing_tables WHERE project_id=%s AND is_live",
            [project_id])
        row = cur.fetchone()
        return row["version"] if row else None


def deployment_honesty(conn: psycopg.Connection, project_id: str) -> dict[int, dict]:
    """Per deployment: how many captured state mutations are side effects, and how many are
    irreversible. This is the schema-enforced honesty (a side effect can never be 'reversible'),
    surfaced so the UI can warn before a rollback that won't fully undo external actions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id AS deployment_id,
                   count(*) FILTER (WHERE sp.mutation_class = 'side_effect')  AS side_effects,
                   count(*) FILTER (WHERE sp.reversibility = 'irreversible')  AS irreversible,
                   count(*)                                                   AS pointers
            FROM controlplane.deployments d
            JOIN controlplane.checkpoints c    ON c.deployment_id = d.id
            JOIN controlplane.state_pointers sp ON sp.checkpoint_id = c.id
            WHERE d.project_id = %s
            GROUP BY d.id
            """,
            [project_id],
        )
        return {r["deployment_id"]: r for r in cur.fetchall()}


def rollback_history(conn: psycopg.Connection, project_id: str, limit: int = 10) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.to_commit_sha, r.status::text AS status, r.initiated_by, r.initiated_at,
                   jsonb_array_length(r.unrollbackable) AS unrollbackable_count
            FROM controlplane.rollbacks r
            WHERE r.project_id = %s
            ORDER BY r.initiated_at DESC
            LIMIT %s
            """,
            [project_id, limit],
        )
        return cur.fetchall()
