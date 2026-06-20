"""The GitHub App webhook receiver: signature verification (security-critical), event routing, and
the gate dispatch. No network - posting is skipped without a token, and the gate runs against a
temp DuckDB. Mirrors how GitHub signs and delivers a pull_request event."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from agentctl.gitops import webhook_app as wh

SECRET = "shh-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---- pure: signature + routing --------------------------------------------------------------- #
def test_verify_signature():
    body = b'{"hello":"world"}'
    assert wh.verify_signature(body, _sign(body), SECRET) is True
    assert wh.verify_signature(body, "sha256=deadbeef", SECRET) is False
    assert wh.verify_signature(body, None, SECRET) is False
    assert wh.verify_signature(body, _sign(body), "") is False     # fail closed without a secret


def test_pr_coords_actionable_and_ignored():
    payload = {"action": "synchronize", "number": 7,
               "repository": {"full_name": "o/r"},
               "pull_request": {"head": {"sha": "abc123"}}}
    assert wh.pr_coords(payload) == {"repo": "o/r", "pr": 7, "sha": "abc123"}

    assert wh.pr_coords({**payload, "action": "closed"}) is None       # non-actionable
    assert wh.pr_coords({"action": "opened"}) is None                  # malformed (no repo/sha)


# ---- the receiver over HTTP ------------------------------------------------------------------ #
def test_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(wh, "GH_WEBHOOK_SECRET", SECRET)
    client = TestClient(wh.app)
    body = json.dumps({"action": "opened"}).encode()
    r = client.post("/webhook", content=body,
                    headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=nope"})
    assert r.status_code == 401


def test_webhook_ignores_non_pr_event(monkeypatch):
    monkeypatch.setattr(wh, "GH_WEBHOOK_SECRET", SECRET)
    client = TestClient(wh.app)
    body = json.dumps({"zen": "ping"}).encode()
    r = client.post("/webhook", content=body,
                    headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": _sign(body)})
    assert r.status_code == 200 and r.json()["status"] == "ignored"


def test_webhook_gates_actionable_pr(monkeypatch, tmp_path):
    # ingest + gate data for PR 321 into a temp store, point the handler at it
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.ingest import ingest_paired
    from agentctl.eval.runner import gate_pr
    from agentctl.storage.duckdb_store import EvalStore

    db = str(tmp_path / "eval.duckdb")
    store = EvalStore.open(db)
    ingest_paired(store, candidate_path="demo/fixtures/candidate.jsonl",
                  baseline_path="demo/fixtures/main.jsonl", commit_sha="abc123",
                  baseline_sha="main", pr_number=321)
    gate_pr(store, 321, GateConfig(nim=0.50, n_min=5))
    store.close()

    monkeypatch.setattr(wh, "GH_WEBHOOK_SECRET", SECRET)
    # route the handler's gate at our temp store + no token (so no network post)
    monkeypatch.setattr(wh, "handle_pull_request",
                        lambda coords, **kw: wh.__wrapped_handle(coords, db=db))

    def _wrapped(coords, db):
        from agentctl.eval.gate import GateConfig as GC
        from agentctl.eval.runner import gate_pr as gp
        from agentctl.storage.duckdb_store import EvalStore as ES
        verdict, _ = gp(ES.open(db), coords["pr"], GC(nim=0.50, n_min=5))
        return {"status": "gated", "pr": coords["pr"], "decision": verdict.decision, "posted": False}
    wh.__wrapped_handle = _wrapped

    client = TestClient(wh.app)
    body = json.dumps({"action": "opened", "number": 321,
                       "repository": {"full_name": "Swa-s-tik/agentctl"},
                       "pull_request": {"head": {"sha": "abc123"}}}).encode()
    r = client.post("/webhook", content=body,
                    headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body)})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "gated" and j["pr"] == 321 and j["decision"] == "ALLOW"


def test_handle_pull_request_skips_without_eval_data(tmp_path):
    from agentctl.storage.duckdb_store import EvalStore
    store = EvalStore.open(str(tmp_path / "empty.duckdb"))
    out = wh.handle_pull_request({"repo": "o/r", "pr": 999, "sha": "x"}, store=store, token="")
    assert out["status"] == "skipped" and out["pr"] == 999
