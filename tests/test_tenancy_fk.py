"""The hard tenancy FK (post-1.0): a deployment must reference a real project row. Proves the
constraint is enforced (orphan project_id rejected) while the seeded bootstrap project works."""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

import agentctl.rollback as rbpkg
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))

_INSERT = ("INSERT INTO controlplane.deployments (project_id, git_commit_sha, created_by) "
           "VALUES (%s, %s, 'test')")


def _schema_conn():
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover - infra-dependent
        pytest.skip(f"no Postgres: {e}")
    apply_schema(conn, _SCHEMA)  # drops + recreates, incl. the seeded bootstrap project
    return conn


def test_deployment_under_seeded_project_is_allowed():
    conn = _schema_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT + " RETURNING id", [DEMO_PROJECT_ID, "fk-ok-sha"])
            assert cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()


def test_deployment_with_orphan_project_is_rejected():
    conn = _schema_conn()
    try:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.cursor() as cur:
                cur.execute(_INSERT, [str(uuid.uuid4()), "fk-bad-sha"])
        conn.rollback()
    finally:
        conn.close()
