"""Dashboard tests: pure HTML render units (no server/DB) + a TestClient integration against a
seeded Postgres that exercises the page and the real 1-click rollback POST."""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.rollback as rbpkg
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.dashboard import queries as q
from agentctl.dashboard import render

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))


# ---------------------------------------------------------------- pure render units (no DB) --- #
def _dep(**kw):
    base = dict(id=3, git_commit_sha="bbbb2222bbbbcccc", status="active", created_by="ci",
                created_at="2026-06-19", weight=10000, is_canary=False, shadow_target=False,
                in_live_table=True)
    base.update(kw)
    return base


def test_deployments_table_shows_commit_status_and_weight():
    html = render.deployments_table([_dep()], {3: {"side_effects": 1, "irreversible": 1, "pointers": 5}})
    assert "bbbb2222bbbb" in html        # short commit
    assert "active" in html
    assert "100%" in html                # live weight
    assert "irreversible" in html        # honesty surfaced


def test_canary_and_shadow_tags_render():
    html = render.deployments_table([_dep(id=4, weight=1000, is_canary=True)], {})
    assert "canary" in html and "10%" in html


def test_rollback_button_only_for_eligible_targets():
    # a non-live ready deploy is a rollback target
    ready = render.deployments_table([_dep(id=2, status="ready", weight=0, in_live_table=False)], {})
    assert "hx-post=\"/api/rollback\"" in ready and "bbbb2222bbbb" in ready
    # the 100%-live active deploy is NOT offered as a rollback target (already serving)
    live = render.deployments_table([_dep(id=3, status="active", weight=10000)], {})
    assert "hx-post" not in live


def test_empty_states():
    assert "No deployments" in render.deployments_table([], {})
    assert "No rollbacks" in render.history_table([])


def test_page_is_self_contained_html():
    html = render.page([_dep()], {}, [], 37, DEMO_PROJECT_ID)
    assert html.startswith("<!doctype html>")
    assert "htmx.org" in html              # the only client dep, from a CDN
    assert "v37" in html                   # live routing version


# ------------------------------------------------------- integration over a seeded Postgres --- #
def _seeded_conn():
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover - infra-dependent
        pytest.skip(f"no Postgres: {e}")
    apply_schema(conn, _SCHEMA)
    from agentctl.rollback.seed import seed
    seed(conn)
    return conn


def test_queries_against_seed():
    conn = _seeded_conn()
    try:
        deps = q.list_deployments(conn, DEMO_PROJECT_ID)
        assert len(deps) >= 2
        # the seed makes B the live 100% arm
        live = [d for d in deps if d["in_live_table"] and d["weight"] >= 10000]
        assert live, "expected a live 100% deployment from the seed"
        honesty = q.deployment_honesty(conn, DEMO_PROJECT_ID)
        assert any(h["irreversible"] for h in honesty.values()), "seed has an irreversible side effect"
        assert q.live_routing_version(conn, DEMO_PROJECT_ID) is not None
    finally:
        conn.close()


def test_index_and_rollback_post():
    conn = _seeded_conn()
    conn.close()
    from fastapi.testclient import TestClient
    from agentctl.dashboard.app import app
    from agentctl.rollback.seed import SHA_A

    client = TestClient(app)

    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "agentctl" in page.text and "Deployments" in page.text

    # 1-click rollback to A (the older sealed deploy) via the htmx POST
    r = client.post("/api/rollback", data={"to_commit_sha": SHA_A})
    assert r.status_code == 200
    assert "Rolled back" in r.text or "could not be undone" in r.text
    # the refreshed fragment re-renders the deployments section
    assert "Rollback history" in r.text
