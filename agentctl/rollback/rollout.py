"""Progressive rollout - the forward complement of rollback. Shift live traffic to a deployment a
slice at a time (canary) or all at once (promote), reusing the SAME atomic, advisory-locked routing
flip that rollback uses (flip_routing / install_weighted), so the gateway re-routes instantly via
pg_notify and never reads a torn table.

This closes the loop: agentctl could already roll *back*; now it can roll *forward* by percentage.
"""
from __future__ import annotations

import psycopg

from agentctl.rollback.routing import flip_routing, install_weighted, live_routing


def _deployment_id(conn: psycopg.Connection, project_id: str, commit_sha: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM controlplane.deployments WHERE project_id=%s AND git_commit_sha=%s",
            [project_id, commit_sha])
        row = cur.fetchone()
    if not row:
        raise ValueError(f"no deployment for commit {commit_sha!r}")
    return row["id"]


def set_canary(conn: psycopg.Connection, project_id: str, commit_sha: str, weight_pct: float,
               *, actor: str = "cli") -> dict:
    """Send ``weight_pct`` of live traffic to ``commit_sha`` as a canary, the rest to the current
    primary; shadow targets are preserved. ``weight_pct >= 100`` is a full promotion (a 100% cutover
    via flip_routing, which also flips deployment statuses). Returns the new live routing + a summary."""
    if not 0 < weight_pct <= 100:
        raise ValueError("weight_pct must be in (0, 100]")
    canary = _deployment_id(conn, project_id, commit_sha)
    weight_bps = round(weight_pct * 100)

    if weight_bps >= 10000:
        _, version = flip_routing(conn, project_id, canary,
                                  reason=f"promote {commit_sha} to 100%", actor=actor)
        mode = "promote"
    else:
        current = live_routing(conn, project_id)
        shadows = [{"deployment_id": r["deployment_id"], "weight": 0, "shadow_target": True}
                   for r in current if r["shadow_target"]]
        primaries = [r for r in current
                     if not r["shadow_target"] and r["deployment_id"] != canary and r["weight"] > 0]
        if not primaries:
            raise ValueError("no current primary to canary against; use a full promotion (weight 100)")
        primary = max(primaries, key=lambda r: r["weight"])["deployment_id"]
        rules = [
            {"deployment_id": primary, "weight": 10000 - weight_bps},
            {"deployment_id": canary, "weight": weight_bps, "is_canary": True},
        ] + shadows
        _, version = install_weighted(conn, project_id, rules,
                                      reason=f"canary {commit_sha} at {weight_pct:g}%", actor=actor)
        # the canary is now a real serving arm - reflect that in its deployment status.
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE controlplane.deployments "
                "SET status='active', activated_at=coalesce(activated_at, now()) WHERE id=%s",
                [canary])
        conn.commit()
        mode = "canary"

    routing = live_routing(conn, project_id)
    return {
        "mode": mode,
        "routing_version": version,
        "weight_pct": weight_pct,
        "routing": [{"commit": r["git_commit_sha"], "weight": r["weight"],
                     "is_canary": r["is_canary"], "shadow": r["shadow_target"]} for r in routing],
    }
