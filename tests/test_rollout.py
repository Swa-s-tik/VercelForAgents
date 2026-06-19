"""Progressive rollout (forward complement of rollback): canary % split then full promote, reusing
the atomic routing flip. Runs against Postgres-in-Docker; self-skips without it."""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.rollback as rbpkg
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.rollback.rollout import set_canary
from agentctl.rollback.seed import SHA_A, SHA_B, seed

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))


def _seeded():
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no Postgres: {e}")
    apply_schema(conn, _SCHEMA)
    seed(conn)  # B is live at 100%, A is a sealed ready deploy
    return conn


def _weights(res):
    return {r["commit"]: (r["weight"], r["is_canary"]) for r in res["routing"]}


def _status(conn, sha):
    with conn.cursor() as cur:
        cur.execute("SELECT status::text AS s FROM controlplane.deployments "
                    "WHERE project_id=%s AND git_commit_sha=%s", [DEMO_PROJECT_ID, sha])
        return cur.fetchone()["s"]


def test_canary_split_then_promote():
    conn = _seeded()
    try:
        # 20% canary to A; B keeps the remaining 80%
        r1 = set_canary(conn, DEMO_PROJECT_ID, SHA_A, 20, actor="t")
        assert r1["mode"] == "canary"
        w = _weights(r1)
        assert w[SHA_A] == (2000, True)
        assert w[SHA_B][0] == 8000
        assert sum(x[0] for x in w.values()) == 10000      # weights still total 100%
        assert _status(conn, SHA_A) == "active"            # canary is now a real serving arm

        # promote A to 100% -> full cutover
        r2 = set_canary(conn, DEMO_PROJECT_ID, SHA_A, 100, actor="t")
        assert r2["mode"] == "promote"
        w2 = _weights(r2)
        assert w2[SHA_A][0] == 10000
        assert SHA_B not in w2                              # B dropped from the live table
        assert _status(conn, SHA_A) == "active" and _status(conn, SHA_B) == "rolled_back"
    finally:
        conn.close()


def test_rollout_validation():
    conn = _seeded()
    try:
        for bad in (0, -5, 150):
            with pytest.raises(ValueError):
                set_canary(conn, DEMO_PROJECT_ID, SHA_A, bad)
            conn.rollback()
        with pytest.raises(ValueError):
            set_canary(conn, DEMO_PROJECT_ID, "no-such-commit", 20)
        conn.rollback()
    finally:
        conn.close()
