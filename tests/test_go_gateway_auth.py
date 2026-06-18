"""Full RBAC enforcement on the compiled Go data plane (post-1.0).

Launches the real Go gateway with AGENTCTL_REQUIRE_KEY=1 + a Postgres DSN (full validation) and
drives its Health RPC over gRPC to prove: a valid key is accepted, and no-key / invalid-key /
wrong-project-key are rejected with the right status code. Skips when the Go binary isn't built or
Postgres is unreachable, so the base/CI Python job (no Go binary) is unaffected.
"""
from __future__ import annotations

from pathlib import Path

import grpc
import pytest

import agentctl.rollback as _rb
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.gateway.go_launcher import binary_available, launch_go_gateway, stop
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()
BOOTSTRAP_KEY = "actl_dev_bootstrap_0000000000000000"
_SCHEMA = str(Path(_rb.__file__).with_name("schema_postgres.sql"))


def _pg_up() -> bool:
    try:
        connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (binary_available() and _pg_up()),
    reason="needs the built Go gateway + reachable Postgres")

OTHER_PROJECT = "00000000-0000-0000-0000-0000000000b1"


def setup_module(module=None):
    conn = connect()
    apply_schema(conn, _SCHEMA)
    # a second tenant + a viewer key in it, to test project isolation
    with conn.cursor() as cur:
        cur.execute("INSERT INTO controlplane.orgs (id, slug) VALUES "
                    "('00000000-0000-0000-0000-0000000000b0','other') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO controlplane.projects (id, org_id, slug) VALUES "
                    "(%s,'00000000-0000-0000-0000-0000000000b0','other') ON CONFLICT DO NOTHING",
                    [OTHER_PROJECT])
        # key 'actl_otherproj_key' -> sha256
        import hashlib
        h = hashlib.sha256(b"actl_otherproj_key").hexdigest()
        cur.execute("INSERT INTO controlplane.api_keys (project_id,name,key_prefix,key_hash,role) "
                    "VALUES (%s,'other','actl_otherpr',%s,'developer') ON CONFLICT (key_hash) DO NOTHING",
                    [OTHER_PROJECT, h])
        # a user bound 'viewer' on the demo project, and a key whose OWN role is owner but is
        # user-bound -> effective role must be viewer (proves the Go join uses the binding).
        cur.execute("INSERT INTO controlplane.users (id, org_id, email) VALUES "
                    "('00000000-0000-0000-0000-0000000000c1','00000000-0000-0000-0000-0000000000a0',"
                    "'binder@example.com') ON CONFLICT DO NOTHING")
        cur.execute("INSERT INTO controlplane.role_bindings (user_id, project_id, role) VALUES "
                    "('00000000-0000-0000-0000-0000000000c1',%s,'viewer') "
                    "ON CONFLICT (user_id, project_id) DO UPDATE SET role='viewer'", [DEMO_PROJECT_ID])
        hb = hashlib.sha256(b"actl_bound_viewer_key").hexdigest()
        cur.execute("INSERT INTO controlplane.api_keys (project_id,user_id,name,key_prefix,key_hash,role) "
                    "VALUES (%s,'00000000-0000-0000-0000-0000000000c1','bound','actl_bound_v',%s,'owner') "
                    "ON CONFLICT (key_hash) DO NOTHING", [DEMO_PROJECT_ID, hb])
    conn.commit()
    conn.close()


def _health(port, key=None):
    md = [("x-api-key", key)] if key else None
    with grpc.insecure_channel(f"localhost:{port}") as ch:
        return dpg.AgentStreamStub(ch).Health(dp.HealthRequest(), metadata=md, timeout=2.0)


def _wait_authed(port, key, tries=40):
    for _ in range(tries):
        try:
            if _health(port, key).ready:
                return True
        except grpc.RpcError:
            pass
        import time
        time.sleep(0.25)
    return False


def test_go_gateway_rbac(monkeypatch):
    from agentctl.runtime.isolated import free_port
    monkeypatch.setenv("AGENTCTL_REQUIRE_KEY", "1")
    monkeypatch.setenv("AGENTCTL_PROJECT_ID", DEMO_PROJECT_ID)
    port = free_port()
    # require-key is on, so the launcher's keyless health check can't confirm readiness.
    proc = launch_go_gateway(port=port, project_id=DEMO_PROJECT_ID, wait_healthy=False)
    try:
        assert _wait_authed(port, BOOTSTRAP_KEY), "gateway never became healthy with a valid key"

        # 1) valid bootstrap key (owner @ demo project) -> accepted
        assert _health(port, BOOTSTRAP_KEY).ready is True

        # 2) no key -> UNAUTHENTICATED
        with pytest.raises(grpc.RpcError) as e:
            _health(port, None)
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

        # 3) garbage key -> UNAUTHENTICATED
        with pytest.raises(grpc.RpcError) as e:
            _health(port, "actl_not_a_real_key")
        assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED

        # 4) valid key but a DIFFERENT project -> PERMISSION_DENIED (tenant isolation)
        with pytest.raises(grpc.RpcError) as e:
            _health(port, "actl_otherproj_key")
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED
    finally:
        stop(proc)


def test_go_gateway_resolves_role_binding(monkeypatch):
    """A key whose own role is owner but is bound to a 'viewer' user must be treated as viewer:
    the Go gateway resolves the role_binding (COALESCE), so a developer floor denies it."""
    from agentctl.runtime.isolated import free_port
    monkeypatch.setenv("AGENTCTL_REQUIRE_KEY", "1")
    monkeypatch.setenv("AGENTCTL_PROJECT_ID", DEMO_PROJECT_ID)
    monkeypatch.setenv("AGENTCTL_MIN_ROLE", "developer")   # floor above viewer
    port = free_port()
    proc = launch_go_gateway(port=port, project_id=DEMO_PROJECT_ID, wait_healthy=False)
    try:
        assert _wait_authed(port, BOOTSTRAP_KEY), "gateway never came up"   # owner standalone passes
        # the user-bound key: own role owner, binding viewer -> effective viewer < developer
        with pytest.raises(grpc.RpcError) as e:
            _health(port, "actl_bound_viewer_key")
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED
    finally:
        stop(proc)
