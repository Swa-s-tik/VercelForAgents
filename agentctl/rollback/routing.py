"""Routing-table operations. The flip is THE one hard ACID transaction (Vertical C)."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # psycopg is used only in type hints (lazy under `from __future__ annotations`),
    import psycopg  # so importing this module does not require psycopg to be installed.


def flip_routing(conn: psycopg.Connection, project_id: str, to_deployment_id: int,
                 *, reason: str, actor: str, notify: bool = True) -> tuple[int, int]:
    """Atomically install a new live routing table sending 100% to ``to_deployment_id``.

    Order matters: demote the current live table FIRST, then insert the new live one, so the
    ``one_live_routing_per_project`` partial-unique index is satisfied at every statement
    (never two live rows). The whole thing is one transaction -> the gateway reads either the
    old version or the new one, never a torn edit. Returns (routing_table_id, version).
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(project_id)])
            cur.execute(
                "SELECT coalesce(max(version),0)+1 AS v FROM controlplane.routing_tables WHERE project_id=%s",
                [project_id])
            version = cur.fetchone()["v"]
            cur.execute(
                "UPDATE controlplane.routing_tables SET is_live=false WHERE project_id=%s AND is_live",
                [project_id])
            cur.execute(
                """INSERT INTO controlplane.routing_tables (project_id, version, is_live, reason, created_by)
                   VALUES (%s,%s,true,%s,%s) RETURNING id""",
                [project_id, version, reason, actor])
            rt_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO controlplane.routing_rules (routing_table_id, deployment_id, weight) VALUES (%s,%s,10000)",
                [rt_id, to_deployment_id])
            cur.execute(
                "UPDATE controlplane.deployments SET status='rolled_back' WHERE project_id=%s AND status='active' AND id<>%s",
                [project_id, to_deployment_id])
            cur.execute(
                "UPDATE controlplane.deployments SET status='active', activated_at=coalesce(activated_at, now()) WHERE id=%s",
                [to_deployment_id])
            if notify:
                # transactional notify: the gateway only learns of the flip if it commits.
                cur.execute("SELECT pg_notify('routing_changed', %s)",
                            [json.dumps({"project": str(project_id), "version": version,
                                         "routing_table_id": rt_id})])
    return rt_id, version


def install_weighted(conn: psycopg.Connection, project_id: str, rules: list[dict],
                     *, reason: str, actor: str, notify: bool = True) -> tuple[int, int]:
    """Install a multi-backend live table (canary/shadow). ``rules`` items:
    {deployment_id, weight, is_canary?, shadow_target?}. Same atomic discipline as flip."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(project_id)])
            cur.execute(
                "SELECT coalesce(max(version),0)+1 AS v FROM controlplane.routing_tables WHERE project_id=%s",
                [project_id])
            version = cur.fetchone()["v"]
            cur.execute(
                "UPDATE controlplane.routing_tables SET is_live=false WHERE project_id=%s AND is_live",
                [project_id])
            cur.execute(
                """INSERT INTO controlplane.routing_tables (project_id, version, is_live, reason, created_by)
                   VALUES (%s,%s,true,%s,%s) RETURNING id""",
                [project_id, version, reason, actor])
            rt_id = cur.fetchone()["id"]
            for r in rules:
                cur.execute(
                    """INSERT INTO controlplane.routing_rules
                       (routing_table_id, deployment_id, weight, is_canary, shadow_target)
                       VALUES (%s,%s,%s,%s,%s)""",
                    [rt_id, r["deployment_id"], r.get("weight", 0),
                     r.get("is_canary", False), r.get("shadow_target", False)])
            if notify:
                cur.execute("SELECT pg_notify('routing_changed', %s)",
                            [json.dumps({"project": str(project_id), "version": version})])
    return rt_id, version


def live_routing(conn: psycopg.Connection, project_id: str) -> list[dict]:
    """The gateway's read: the single live table, resolved to weighted backends.
    Guaranteed one consistent version by the partial-unique index."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT rr.deployment_id, rr.weight, rr.is_canary, rr.shadow_target,
                      d.git_commit_sha, rt.version
               FROM controlplane.routing_tables rt
               JOIN controlplane.routing_rules rr ON rr.routing_table_id = rt.id
               JOIN controlplane.deployments d    ON d.id = rr.deployment_id
               WHERE rt.project_id = %s AND rt.is_live
               ORDER BY rr.weight DESC""",
            [project_id])
        return cur.fetchall()
