"""Progressive rollout - the forward complement of rollback. Shift live traffic to a deployment a
slice at a time (canary) or all at once (promote), reusing the SAME atomic, advisory-locked routing
flip that rollback uses (flip_routing / install_weighted), so the gateway re-routes instantly via
pg_notify and never reads a torn table.

This closes the loop: agentctl could already roll *back*; now it can roll *forward* by percentage.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentctl.rollback.routing import flip_routing, install_weighted, live_routing

if TYPE_CHECKING:  # psycopg is used only in type hints (lazy under `from __future__ annotations`),
    import psycopg  # so the pure helpers here (e.g. _split_primaries) import without psycopg installed.


def _deployment_id(conn: psycopg.Connection, project_id: str, commit_sha: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM controlplane.deployments WHERE project_id=%s AND git_commit_sha=%s",
            [project_id, commit_sha])
        row = cur.fetchone()
    if not row:
        raise ValueError(f"no deployment for commit {commit_sha!r}")
    return row["id"]


def _split_primaries(primaries: list[dict], budget_bps: int) -> list[dict]:
    """Rescale existing primary weights proportionally to fill ``budget_bps``, preserving their
    relative split, instead of collapsing to the heaviest arm. Returns rule dicts whose weights sum to
    exactly ``budget_bps`` (rounding drift is absorbed by the largest arm). Pure (no I/O) - unit-tested."""
    total = sum(p["weight"] for p in primaries)
    rules = [{"deployment_id": p["deployment_id"], "weight": round(budget_bps * p["weight"] / total)}
             for p in primaries]
    drift = budget_bps - sum(r["weight"] for r in rules)
    if drift and rules:
        i = max(range(len(rules)), key=lambda k: rules[k]["weight"])
        rules[i]["weight"] += drift
    return rules


def set_canary(conn: psycopg.Connection, project_id: str, commit_sha: str, weight_pct: float,
               *, actor: str = "cli") -> dict:
    """Send ``weight_pct`` of live traffic to ``commit_sha`` as a canary, the rest to the current
    primary; shadow targets are preserved. ``weight_pct >= 100`` is a full promotion (a 100% cutover
    via flip_routing, which also flips deployment statuses). Returns the new live routing + a summary."""
    if not 0 < weight_pct <= 100:
        raise ValueError("weight_pct must be in (0, 100]")
    canary = _deployment_id(conn, project_id, commit_sha)

    if weight_pct >= 100:
        _, version = flip_routing(conn, project_id, canary,
                                  reason=f"promote {commit_sha} to 100%", actor=actor)
        mode = "promote"
    else:
        weight_bps = round(weight_pct * 100)
        if weight_bps <= 0:
            raise ValueError(f"weight_pct {weight_pct:g} rounds to 0 bps; use a larger canary weight")
        weight_bps = min(weight_bps, 9999)  # a canary is never a 100% rule (100% is a promotion)
        current = live_routing(conn, project_id)
        shadows = [{"deployment_id": r["deployment_id"], "weight": 0, "shadow_target": True}
                   for r in current if r["shadow_target"]]
        primaries = [r for r in current
                     if not r["shadow_target"] and r["deployment_id"] != canary and r["weight"] > 0]
        if not primaries:
            raise ValueError("no current primary to canary against; use a full promotion (weight 100)")
        # Preserve EVERY existing primary, rescaled proportionally into the remaining budget, rather
        # than collapsing to the heaviest arm (which silently drops the others' live traffic).
        rules = _split_primaries(primaries, 10000 - weight_bps)
        rules.append({"deployment_id": canary, "weight": weight_bps, "is_canary": True})
        rules += shadows
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


def gated_rollout(conn: psycopg.Connection, project_id: str, commit_sha: str, weight_pct: float,
                  *, gate_pr: int, gate_db: str | None = None, nim: float = 0.50, n_min: int = 100,
                  actor: str = "cli") -> tuple[object, dict | None]:
    """The safety interlock: run the eval gate for ``gate_pr`` and roll out ONLY if it ALLOWs. Returns
    ``(verdict, rollout_result | None)`` - the rollout is None (and no routing change happens) when the
    gate is anything but ALLOW, so a regression can never be promoted by mistake."""
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.runner import gate_pr as run_gate
    from agentctl.storage.duckdb_store import EvalStore

    store = EvalStore.open(gate_db) if gate_db else EvalStore.open()
    try:  # read-only gate; close before the routing write so we never hold the DuckDB handle across it
        verdict, _ = run_gate(store, gate_pr, GateConfig(nim=nim, n_min=n_min))
    finally:
        store.close()
    if verdict.decision != "ALLOW":
        return verdict, None
    return verdict, set_canary(conn, project_id, commit_sha, weight_pct, actor=actor)
