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
    assert "hx-post=\"/api/rollback/bbbb2222bbbbcccc\"" in ready
    # the 100%-live active deploy is NOT offered as a rollback target (already serving)
    live = render.deployments_table([_dep(id=3, status="active", weight=10000)], {})
    assert "hx-post" not in live


def test_empty_states():
    assert "No deployments" in render.deployments_table([], {})
    assert "No rollbacks" in render.history_table([])
    assert "No gateway traffic" in render.traffic_table([])


def test_traffic_table_renders_arms():
    rows = [{"arm": "vA", "streams": 12, "frames": 252, "shadow_dropped": 3, "avg_latency_ms": 47.0},
            {"arm": "vB", "streams": 2, "frames": 42, "shadow_dropped": 0, "avg_latency_ms": 51.0}]
    html = render.traffic_table(rows)
    assert "vA" in html and ">12<" in html and "47 ms" in html and "3 dropped" in html
    assert "vB" in html and "51 ms" in html


def test_page_is_self_contained_html():
    html = render.page([_dep()], {}, [], 37, DEMO_PROJECT_ID)
    assert html.startswith("<!doctype html>")
    assert "htmx.org" in html              # the only client dep, from a CDN
    assert "v37" in html                   # live routing version
    assert "eval verdict" in html          # the eval surface is joined into the deploy view


def test_match_verdict_exact_and_prefix():
    verdicts = {"aaaa1111aaaa2222": {"decision": "ALLOW"}}
    assert render.match_verdict("aaaa1111aaaa2222", verdicts)["decision"] == "ALLOW"   # exact
    assert render.match_verdict("aaaa1111aaaa2222ffff", verdicts)["decision"] == "ALLOW"  # dep sha longer
    assert render.match_verdict("aaaa1111", verdicts)["decision"] == "ALLOW"  # 8-char prefix matches
    assert render.match_verdict("aaaa11", verdicts) is None        # < 8 shared chars -> no match
    assert render.match_verdict("bbbb9999", verdicts) is None      # no overlap
    assert render.match_verdict("x", {}) is None


def test_verdict_cell_in_table():
    verdicts = {"bbbb2222bbbbcccc": {"decision": "ALLOW", "win_rate": 0.68, "wilson_low": 0.53,
                                     "wilson_high": 0.80, "n": 41, "suites": 3}}
    html = render.deployments_table([_dep()], {}, verdicts)
    assert "ALLOW" in html and "x3" in html and "[0.53, 0.80]" in html
    # a BLOCK verdict renders distinctly
    block = render.deployments_table([_dep()], {}, {"bbbb2222bbbbcccc": {"decision": "BLOCK",
              "win_rate": 0.43, "wilson_low": 0.34, "wilson_high": 0.52, "n": 111, "suites": 1}})
    assert "BLOCK" in block


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


def test_verdicts_by_commit_from_duckdb(tmp_path):
    """Populate a DuckDB eval store (ingest + gate) and read the aggregate verdict back by commit."""
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.ingest import ingest_paired
    from agentctl.eval.runner import gate_pr
    from agentctl.storage.duckdb_store import EvalStore

    db = str(tmp_path / "eval.duckdb")
    store = EvalStore.open(db)
    ingest_paired(store, candidate_path="demo/fixtures/candidate.jsonl",
                  baseline_path="demo/fixtures/main.jsonl", commit_sha="aaaa1111aaaa2222",
                  baseline_sha="main", pr_number=777)
    gate_pr(store, 777, GateConfig(nim=0.50, n_min=5))  # persists a gate_result per suite
    store.close()

    v = q.verdicts_by_commit(db)
    assert "aaaa1111aaaa2222" in v
    row = v["aaaa1111aaaa2222"]
    assert row["decision"] == "ALLOW" and row["suites"] >= 1 and row["wilson_low"] is not None
    # the join shows up in a rendered table when a deployment shares that commit
    dep = _dep(git_commit_sha="aaaa1111aaaa2222", status="active")
    assert "ALLOW" in render.deployments_table([dep], {}, v)


def test_verdicts_by_commit_missing_db_is_empty():
    assert q.verdicts_by_commit("/nonexistent/path/eval.duckdb") == {}


def test_stream_telemetry_aggregates_by_arm():
    from psycopg.types.json import Json
    conn = _seeded_conn()
    try:
        # (arm, frames_out, shadow_dropped, start_ns, end_ns) - vA twice, vB once
        specs = [("vA", 21.0, 0.0, 0, 50_000_000),
                 ("vA", 30.0, 1.0, 0, 70_000_000),
                 ("vB", 10.0, 0.0, 0, 40_000_000)]
        with conn.cursor() as cur:
            for i, (arm, fr, dr, st, en) in enumerate(specs):
                cur.execute(
                    "INSERT INTO controlplane.otel_spans "
                    "(trace_id, span_id, project_id, name, kind, start_unixnano, end_unixnano, "
                    " status_code, attributes) "
                    "VALUES (%s,%s,%s,'gateway.stream.metrics',2,%s,%s,0,%s)",
                    [bytes([i]) * 16, bytes([i]) * 8, DEMO_PROJECT_ID, st, en,
                     Json({"canary_arm": arm, "measure.frames_out": fr, "measure.shadow_dropped": dr})])
        conn.commit()

        rows = {r["arm"]: r for r in q.stream_telemetry(conn, DEMO_PROJECT_ID)}
        assert rows["vA"]["streams"] == 2 and float(rows["vA"]["frames"]) == 51.0
        assert float(rows["vA"]["shadow_dropped"]) == 1.0
        assert float(rows["vA"]["avg_latency_ms"]) == pytest.approx(60.0)  # (50+70)/2 ms
        assert rows["vB"]["streams"] == 1 and float(rows["vB"]["frames"]) == 10.0
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

    # 1-click rollback to A (the older sealed deploy) via the htmx POST (sha in the path)
    r = client.post(f"/api/rollback/{SHA_A}")
    assert r.status_code == 200
    assert "Rolled back" in r.text or "could not be undone" in r.text
    # the refreshed fragment re-renders the deployments section
    assert "Rollback history" in r.text
