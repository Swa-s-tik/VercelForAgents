"""API keys + RBAC (Workstream 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.rollback as _rb
from agentctl.auth.keys import BOOTSTRAP_KEY, generate_key, hash_key, key_prefix
from agentctl.auth.principal import AuthError, Principal, resolve_principal
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID

_SCHEMA = str(Path(_rb.__file__).with_name("schema_postgres.sql"))


def setup_module(module=None):
    conn = connect()
    apply_schema(conn, _SCHEMA)
    conn.close()


# ---- key primitives ----
def test_generate_and_hash():
    secret, prefix, h = generate_key()
    assert secret.startswith("actl_") and len(secret) > 40
    assert prefix == secret[:12]
    assert h == hash_key(secret) and len(h) == 64
    # distinct each time
    assert generate_key()[0] != generate_key()[0]


# ---- role ranking ----
def test_role_enforcement():
    viewer = Principal(DEMO_PROJECT_ID, "viewer")
    owner = Principal(DEMO_PROJECT_ID, "owner")
    assert owner.require("admin") is owner          # owner outranks admin
    assert viewer.require("viewer") is viewer
    with pytest.raises(AuthError):
        viewer.require("admin")
    with pytest.raises(AuthError):
        Principal(DEMO_PROJECT_ID, "developer").require("owner")


# ---- backward-compat keystone ----
def test_no_key_resolves_bootstrap_owner():
    p = resolve_principal(None, None)               # no DB access on the None path
    assert p.project_id == DEMO_PROJECT_ID and p.role == "owner"


def test_bootstrap_key_resolves():
    conn = connect()
    try:
        p = resolve_principal(conn, BOOTSTRAP_KEY)
        assert p.project_id == DEMO_PROJECT_ID and p.role == "owner" and p.name == "bootstrap"
    finally:
        conn.close()


# ---- DB-backed resolution + revocation ----
def _insert_key(conn, role: str):
    secret, prefix, h = generate_key()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO controlplane.api_keys (project_id,name,key_prefix,key_hash,role) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id", [DEMO_PROJECT_ID, "t", prefix, h, role])
        kid = cur.fetchone()["id"]
    conn.commit()
    return secret, kid


def test_db_key_resolution_and_role():
    conn = connect()
    try:
        secret, _ = _insert_key(conn, "viewer")
        p = resolve_principal(conn, secret)
        assert p.project_id == DEMO_PROJECT_ID and p.role == "viewer"
        with pytest.raises(AuthError):
            p.require("admin")                       # viewer cannot rollback
    finally:
        conn.close()


def test_invalid_key_rejected():
    conn = connect()
    try:
        with pytest.raises(AuthError):
            resolve_principal(conn, "actl_not_a_real_key")
    finally:
        conn.close()


def test_revoked_key_rejected():
    conn = connect()
    try:
        secret, kid = _insert_key(conn, "developer")
        assert resolve_principal(conn, secret).role == "developer"
        with conn.cursor() as cur:
            cur.execute("UPDATE controlplane.api_keys SET revoked_at=now() WHERE id=%s", [kid])
        conn.commit()
        with pytest.raises(AuthError):
            resolve_principal(conn, secret)
    finally:
        conn.close()
