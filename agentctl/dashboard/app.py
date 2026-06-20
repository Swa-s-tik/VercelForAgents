"""FastAPI app for the dashboard. Server-rendered HTML + htmx; the only write path is the 1-click
rollback, which calls the real rollback_to_commit orchestrator (atomic flip + idempotent state
realignment) - the same code path as `agentctl rollback run`.

This is a local operator tool: bind it to localhost. It reads/writes the controlplane SoR directly.
The write paths (/api/rollback, /api/rollout) and /api/state are gated by the API-key dependency
(roles admin / developer / viewer). Set AGENTCTL_REQUIRE_KEY=1 - mandatory for any non-loopback bind -
so the no-key bootstrap-owner fallback can't leave a public bind open to forged/CSRF requests.

Run: uvicorn agentctl.dashboard.app:app --port 8050   (or: agentctl dashboard)
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from agentctl.auth.fastapi_dep import principal_dep
from agentctl.common.db import connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.dashboard import queries as q
from agentctl.dashboard import render
from agentctl.rollback.rollback import rollback_to_commit
from agentctl.rollback.rollout import set_canary

app = FastAPI(title="agentctl dashboard")


def _project_id() -> str:
    return DEMO_PROJECT_ID


def _snapshot(conn, project_id: str) -> dict:
    return {
        "deployments": q.list_deployments(conn, project_id),
        "honesty": q.deployment_honesty(conn, project_id),
        "history": q.rollback_history(conn, project_id),
        "routing_version": q.live_routing_version(conn, project_id),
        "verdicts": q.verdicts_by_commit(),  # eval surface joined to the deploy surface (DuckDB)
        "traffic": q.stream_telemetry(conn, project_id),  # data-plane traffic from otel_spans
        "routing": q.routing_history(conn, project_id),   # every rollback/canary/promote, one timeline
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/api/state", dependencies=[Depends(principal_dep("viewer"))])
def state():
    """Machine-readable control-plane state (deployments, eval verdicts, traffic, routing timeline)."""
    project_id = _project_id()
    conn = connect()
    try:
        return q.json_snapshot(conn, project_id)
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def index():
    project_id = _project_id()
    conn = connect()
    try:
        snap = _snapshot(conn, project_id)
    finally:
        conn.close()
    return render.page(project_id=project_id, **snap)


@app.get("/api/dashboard", response_class=HTMLResponse)
def dashboard_fragment():
    """The inner #dash region, for htmx auto-refresh (polled every few seconds) so the live-traffic
    and routing panels update without a full reload."""
    project_id = _project_id()
    conn = connect()
    try:
        snap = _snapshot(conn, project_id)
    finally:
        conn.close()
    return render.dashboard_inner(**snap)


@app.post("/api/rollback/{to_commit_sha}", response_class=HTMLResponse,
          dependencies=[Depends(principal_dep("admin"))])
def rollback(to_commit_sha: str):
    """Roll back to a sealed deployment, then re-render the dashboard region (htmx swaps #dash).
    The commit sha is a path param (URL-safe hex), so no form body / python-multipart is needed."""
    project_id = _project_id()
    conn = connect()
    try:
        try:
            result = rollback_to_commit(conn, project_id, to_commit_sha, actor="dashboard")
            unrb = len(result.get("unrollbackable", []) or [])
            flash = (f"Rolled back to {to_commit_sha[:12]} - status {result.get('status', '?')}"
                     + (f"; {unrb} side effect(s) could not be undone (irreversible)." if unrb else "."))
            flash_html = f'<div class="flash">{flash}</div>'
        except Exception as e:  # surface the failure in the UI instead of a 500
            flash_html = f'<div class="flash err">Rollback failed: {render._esc(e)}</div>'
            snap = _snapshot(conn, project_id)
            return HTMLResponse(flash_html + render.dashboard_inner(**snap))
        snap = _snapshot(conn, project_id)
    finally:
        conn.close()
    return HTMLResponse(flash_html + render.dashboard_inner(**snap))


@app.post("/api/rollout/{to_commit_sha}/{weight}", response_class=HTMLResponse,
          dependencies=[Depends(principal_dep("developer"))])
def rollout(to_commit_sha: str, weight: float):
    """Forward rollout (canary or promote) to a deployment, then re-render the dashboard region."""
    project_id = _project_id()
    conn = connect()
    try:
        try:
            res = set_canary(conn, project_id, to_commit_sha, weight, actor="dashboard")
            flash_html = (f'<div class="flash">Rollout ({res["mode"]}) {to_commit_sha[:12]} -> '
                          f'{weight:g}% - routing v{res["routing_version"]}.</div>')
        except Exception as e:
            snap = _snapshot(conn, project_id)
            return HTMLResponse(f'<div class="flash err">Rollout failed: {render._esc(e)}</div>'
                                + render.dashboard_inner(**snap))
        snap = _snapshot(conn, project_id)
    finally:
        conn.close()
    return HTMLResponse(flash_html + render.dashboard_inner(**snap))
