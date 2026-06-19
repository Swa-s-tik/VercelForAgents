"""The watch-loop controller: a thin kopf wrapper that turns `kubectl apply` of an AgentDeployment
into a real rollout by calling the (tested) reconcile. The control logic lives in `reconcile_body`,
which needs no kopf, so it is unit-tested directly; the kopf decorators just wire watch -> reconcile
-> status. Run with `kopf run -m agentctl.operator.controller` or `agentctl operator run`. Needs the
optional `kopf` dep (`pip install 'agentctl[operator]'`).
"""
from __future__ import annotations

import psycopg

from agentctl.common.db import connect
from agentctl.operator.reconcile import reconcile_agentdeployment


def reconcile_body(body, conn: psycopg.Connection | None = None) -> dict:
    """Reconcile one AgentDeployment body (the full resource dict kopf hands us) and return the status
    to write back. Opens its own connection if none is passed. This is the controller's whole brain -
    everything else is kopf plumbing."""
    own = conn is None
    if own:
        conn = connect()
    try:
        return reconcile_agentdeployment(conn, dict(body))
    finally:
        if own:
            conn.close()


def _register() -> None:
    """Register the create/update handlers with kopf. Called on import so `kopf run -m <this>` and
    `agentctl operator run` both discover them; a no-op import-guarded so the module imports without
    kopf (the reconcile_body core and `agentctl apply` stay usable)."""
    import kopf

    @kopf.on.create("agentctl.dev", "v1alpha1", "agentdeployments")
    @kopf.on.update("agentctl.dev", "v1alpha1", "agentdeployments")
    def reconcile_cr(body, patch, logger, **_):
        status = reconcile_body(body)
        patch.status.update(status)            # kopf persists this to the CR's .status subresource
        logger.info("AgentDeployment %s -> %s",
                    (body.get("metadata") or {}).get("name"), status.get("phase"))
        return status


try:  # kopf is optional; importing this module never fails without it
    _register()
except ImportError:
    pass
