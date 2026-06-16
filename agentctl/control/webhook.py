"""Local git push webhook emulator (Phase 2).

Parses a simulated GitHub-style push payload, extracts the commit hash, and registers a
pending deployment sequence in Postgres (queued -> building -> ready). Optionally provisions
an isolated preview agent (Phase 1 runtime) and records its endpoint in build_meta, so the
gateway's PG route cache (Phase 3) can route to it.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import FastAPI, HTTPException, Request
from psycopg.types.json import Json

from agentctl.common.db import connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.runtime.isolated import (
    IsolatedRuntime,
    RuntimeHandle,
    RuntimeSpec,
    free_port,
    provision_preview,
)

WEBHOOK_SECRET = os.environ.get("AGENTCTL_WEBHOOK_SECRET", "")

# control plane's record of provisioned previews (so they can be torn down)
_ACTIVE_PREVIEWS: dict[int, RuntimeHandle] = {}


def make_push_payload(sha: str, ref: str = "refs/heads/feature", repo: str = "agentctl-demo",
                      changed: list[str] | None = None, version_tag: str = "vP") -> dict:
    return {
        "ref": ref, "after": sha, "repository": {"name": repo},
        "head_commit": {"id": sha, "modified": changed or ["prompts/agent.yaml", "graph.json"]},
        "version_tag": version_tag,
    }


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not WEBHOOK_SECRET:
        return True
    mac = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, signature or "")


def _set_status(conn, dep_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE controlplane.deployments SET status=%s WHERE id=%s", [status, dep_id])
    conn.commit()


def _merge_build_meta(conn, dep_id: int, extra: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE controlplane.deployments SET build_meta = build_meta || %s::jsonb WHERE id=%s",
                    [Json(extra), dep_id])
    conn.commit()


def handle_push(conn, payload: dict, *, project_id: str = DEMO_PROJECT_ID,
                runtime: IsolatedRuntime | None = None, provision: bool = False,
                agent_kind: str = "echo") -> dict:
    """Register a pending deployment from a push payload, advancing queued->building->ready."""
    sha = payload.get("after") or payload.get("head_commit", {}).get("id")
    if not sha:
        raise ValueError("payload missing commit sha")
    ref = payload.get("ref", "")
    repo = payload.get("repository", {}).get("name", "")
    changed = payload.get("head_commit", {}).get("modified", [])
    version_tag = payload.get("version_tag", "vP")

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO controlplane.deployments
               (project_id, git_commit_sha, git_ref, status, created_by, build_meta)
               VALUES (%s,%s,%s,'queued','webhook',%s)
               ON CONFLICT (project_id, git_commit_sha)
               DO UPDATE SET status='queued', git_ref=EXCLUDED.git_ref
               RETURNING id""",
            [project_id, sha, ref, Json({"repo": repo, "changed": changed, "version_tag": version_tag})])
        dep_id = cur.fetchone()["id"]
    conn.commit()

    sequence = ["queued"]
    _set_status(conn, dep_id, "building")
    sequence.append("building")

    endpoint = None
    if provision and runtime is not None:
        handle = provision_preview(
            runtime, RuntimeSpec(name=f"preview-{sha[:8]}", port=free_port(),
                                 version_tag=version_tag, kind=agent_kind))
        endpoint = handle.endpoint
        _ACTIVE_PREVIEWS[dep_id] = handle
        _merge_build_meta(conn, dep_id,
                          {"endpoint": endpoint, "runtime": handle.kind, "runtime_id": handle.id})

    _set_status(conn, dep_id, "ready")
    sequence.append("ready")
    return {"deployment_id": dep_id, "commit_sha": sha, "ref": ref, "status": "ready",
            "endpoint": endpoint, "sequence": sequence, "changed": changed}


def teardown_preview(runtime: IsolatedRuntime, dep_id: int) -> None:
    handle = _ACTIVE_PREVIEWS.pop(dep_id, None)
    if handle:
        runtime.teardown(handle)


# --------------------------------------------------------------------------- #
# FastAPI app (run: uvicorn agentctl.control.webhook:app --port 8088)
# --------------------------------------------------------------------------- #
app = FastAPI(title="agentctl git webhook")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook/git")
async def webhook_git(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    conn = connect()
    try:
        return handle_push(conn, payload, provision=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
