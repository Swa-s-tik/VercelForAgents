"""FastAPI app for the dashboard. Server-rendered HTML + htmx; the only write path is the 1-click
rollback, which calls the real rollback_to_commit orchestrator (atomic flip + idempotent state
realignment) - the same code path as `agentctl rollback run`.

This is a local operator tool: bind it to localhost. It reads/writes the controlplane SoR directly.

Run: uvicorn agentctl.dashboard.app:app --port 8050   (or: agentctl dashboard)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agentctl.common.db import connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.dashboard import queries as q
from agentctl.dashboard import render
from agentctl.rollback.rollback import rollback_to_commit

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
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    project_id = _project_id()
    conn = connect()
    try:
        snap = _snapshot(conn, project_id)
    finally:
        conn.close()
    return render.page(project_id=project_id, **snap)


@app.post("/api/rollback/{to_commit_sha}", response_class=HTMLResponse)
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
