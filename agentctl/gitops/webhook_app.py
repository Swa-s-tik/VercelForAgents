"""The hosted GitHub App, as code: a signed webhook receiver that gates a PR on `pull_request` events
and posts the verdict back. This is the App's brain - verify GitHub's HMAC, route the event, dispatch
to the eval-gate. Hosting it (a public URL, the App registration + per-install tokens) is the ops step
documented in docs/design/GITHUB_APP.md; the reusable Action already covers the CI-triggered path.

The security-critical pieces (signature verification, event routing) are pure and unit-tested; the
gate dispatch reuses gate_pr + github_gate.

Run: uvicorn agentctl.gitops.webhook_app:app --port 8099   (or: agentctl gitops-app)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

from fastapi import FastAPI, HTTPException, Request

from agentctl.config import GH_TOKEN, GH_WEBHOOK_SECRET

# PR actions worth gating (a new PR or a new push to one); others are ignored.
ACTIONABLE = {"opened", "synchronize", "reopened", "ready_for_review"}

app = FastAPI(title="agentctl github app")


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """GitHub's X-Hub-Signature-256 (HMAC-SHA256 of the raw body). Fail closed when no secret is
    configured - an unauthenticated webhook endpoint must not act."""
    if not secret:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, signature or "")


def pr_coords(payload: dict) -> dict | None:
    """Extract {repo, pr, sha} for an actionable pull_request event; None to ignore (closed, draft
    without ready_for_review, or a malformed payload)."""
    if payload.get("action") not in ACTIONABLE:
        return None
    pr = payload.get("pull_request") or {}
    number = payload.get("number") or pr.get("number")
    repo = (payload.get("repository") or {}).get("full_name")
    sha = (pr.get("head") or {}).get("sha")
    if not (number and repo and sha):
        return None
    return {"repo": repo, "pr": int(number), "sha": sha}


def handle_pull_request(coords: dict, *, store=None, token: str | None = None, opener=None) -> dict:
    """Gate the PR against its ingested eval runs and post the verdict back (best-effort). Returns a
    summary. If there are no eval runs for the PR yet, it is skipped (the CI ingests them first)."""
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.runner import gate_pr
    from agentctl.storage.duckdb_store import EvalStore
    from agentctl.gitops import github_gate as gh

    store = store or EvalStore.open()
    try:
        verdict, decisions = gate_pr(store, coords["pr"], GateConfig())
    except Exception:
        return {"status": "skipped", "pr": coords["pr"], "reason": "no eval runs for this PR"}
    if not decisions:   # the CI ingests eval runs first; nothing to gate yet
        return {"status": "skipped", "pr": coords["pr"], "reason": "no eval runs for this PR"}

    token = token if token is not None else GH_TOKEN
    posted = False
    if token:
        try:
            target = gh.GitHubTarget(repo=coords["repo"], sha=coords["sha"], token=token, pr=coords["pr"])
            payload = gh.status_payload(verdict.decision, verdict.reason, verdict.exit_code)
            gh.post_commit_status(target, payload, *( (opener,) if opener else () ))
            gh.post_pr_comment(target, gh.comment_markdown(verdict, decisions, sha=coords["sha"]),
                               *( (opener,) if opener else () ))
            posted = True
        except Exception:
            posted = False
    return {"status": "gated", "pr": coords["pr"], "decision": verdict.decision, "posted": posted}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), GH_WEBHOOK_SECRET):
        raise HTTPException(401, "invalid or missing signature")
    if request.headers.get("X-GitHub-Event") != "pull_request":
        return {"status": "ignored", "event": request.headers.get("X-GitHub-Event")}
    coords = pr_coords(json.loads(body))
    if coords is None:
        return {"status": "ignored", "reason": "non-actionable pull_request"}
    # handle_pull_request does blocking I/O (DuckDB gate + two urlopen() calls, up to 15s each). Run it
    # off the event loop so it can't stall every other concurrent webhook/health request for ~30s.
    return await asyncio.to_thread(handle_pull_request, coords)
