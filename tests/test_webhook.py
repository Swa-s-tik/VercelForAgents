"""Webhook emulator tests (Phase 2). Needs Postgres. Runs under pytest or as a script."""
from __future__ import annotations

from pathlib import Path

import agentctl.rollback as rb
from agentctl.common.db import apply_schema, connect
from agentctl.control.webhook import handle_push, make_push_payload, teardown_preview
from agentctl.runtime.isolated import ProcessRuntime, grpc_agent_health

_SCHEMA = str(Path(rb.__file__).with_name("schema_postgres.sql"))


def setup_module(module=None):
    conn = connect()
    apply_schema(conn, _SCHEMA)
    conn.close()


def test_push_registers_deployment():
    conn = connect()
    res = handle_push(conn, make_push_payload("deadbeef1234", changed=["prompts/x.yaml"]))
    assert res["status"] == "ready"
    assert res["sequence"] == ["queued", "building", "ready"]
    with conn.cursor() as cur:
        cur.execute("SELECT status, build_meta FROM controlplane.deployments WHERE git_commit_sha=%s",
                    ["deadbeef1234"])
        row = cur.fetchone()
    assert row["status"] == "ready"
    assert row["build_meta"]["changed"] == ["prompts/x.yaml"]
    conn.close()


def test_push_provisions_preview():
    conn = connect()
    rt = ProcessRuntime()
    res = handle_push(conn, make_push_payload("cafe00010203"), provision=True, runtime=rt)
    try:
        assert res["endpoint"], "preview endpoint not assigned"
        assert grpc_agent_health(res["endpoint"], 10), "provisioned preview not healthy"
        with conn.cursor() as cur:
            cur.execute("SELECT build_meta FROM controlplane.deployments WHERE git_commit_sha=%s",
                        ["cafe00010203"])
            bm = cur.fetchone()["build_meta"]
        assert bm["endpoint"] == res["endpoint"] and bm["runtime"] == "process"
    finally:
        teardown_preview(rt, res["deployment_id"])
        conn.close()


def test_http_route_optional():
    try:
        from fastapi.testclient import TestClient
    except Exception as e:
        print(f"  TestClient unavailable ({e}); skipping HTTP route test")
        return
    from agentctl.control.webhook import app
    r = TestClient(app).post("/webhook/git", json=make_push_payload("abcdef000111"))
    assert r.status_code == 200 and r.json()["status"] == "ready"


if __name__ == "__main__":
    setup_module()
    test_push_registers_deployment(); print("  ok  register")
    test_push_provisions_preview(); print("  ok  provision preview")
    test_http_route_optional(); print("  ok  http route (or skipped)")
    print("webhook tests passed")
