"""Reconcile one AgentDeployment custom resource: drive live routing to match `spec`.

This is the operator's core - a pure function from (desired CR, control plane) to an applied state +
a status. A watch-loop controller (kopf / controller-runtime) or the hosted GitHub App just calls
this; everything it needs (the gate, canary, promote) already exists, so the reconcile is small.

CR shape (agentctl.dev/v1alpha1):
    spec.commit         (required) deployment commit sha to drive traffic toward
    spec.weight         percent of live traffic, 0..100 (default 100 = full promote)
    spec.requireGatePR  if set, roll out only when that PR's eval gate ALLOWs
    spec.nim            non-inferiority margin for requireGatePR (default 0.50)
    spec.project        project id (default: the bootstrap/demo project)
"""
from __future__ import annotations

import psycopg

from agentctl.config import DEMO_PROJECT_ID

API_VERSION = "agentctl.dev/v1alpha1"
KIND = "AgentDeployment"


def reconcile_agentdeployment(conn: psycopg.Connection, cr: dict, *, actor: str = "operator") -> dict:
    """Apply one AgentDeployment CR. Returns a status dict (the `.status` the controller would write):
    phase Live (rolled out), Blocked (gate failed -> no change), with mode/routingVersion/gate."""
    if cr.get("apiVersion") not in (API_VERSION, None):
        raise ValueError(f"unsupported apiVersion {cr.get('apiVersion')!r} (want {API_VERSION})")
    if cr.get("kind") != KIND:
        raise ValueError(f"not an {KIND}: kind={cr.get('kind')!r}")
    spec = cr.get("spec") or {}
    commit = spec.get("commit")
    if not commit:
        raise ValueError("spec.commit is required")

    weight = float(spec.get("weight", 100))
    project = spec.get("project") or DEMO_PROJECT_ID
    gate_pr = spec.get("requireGatePR")
    nim = float(spec.get("nim", 0.50))

    from agentctl.rollback.rollout import gated_rollout, set_canary

    if gate_pr is not None:
        verdict, res = gated_rollout(conn, project, commit, weight,
                                     gate_pr=int(gate_pr), nim=nim, actor=actor)
        if res is None:
            return {"phase": "Blocked", "gate": verdict.decision, "reason": verdict.reason}
        return {"phase": "Live", "mode": res["mode"], "routingVersion": res["routing_version"],
                "weight": weight, "gate": "ALLOW"}

    res = set_canary(conn, project, commit, weight, actor=actor)
    return {"phase": "Live", "mode": res["mode"], "routingVersion": res["routing_version"],
            "weight": weight}
