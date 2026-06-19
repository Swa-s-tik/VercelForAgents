"""The declarative surface: reconcile an AgentDeployment CR against a seeded control plane. Verifies
the canary path, the gate interlock (Blocked -> no routing change), and CR validation. The CRD YAML
is also schema-checked. Runs against Postgres-in-Docker; self-skips without it."""
from __future__ import annotations

from pathlib import Path

import pytest

import agentctl.rollback as rbpkg
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.operator.reconcile import reconcile_agentdeployment
from agentctl.rollback.routing import live_routing
from agentctl.rollback.seed import SHA_A, seed

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))
_CRD = Path(__file__).resolve().parents[1] / "deploy" / "crds" / "agentdeployment-crd.yaml"
_SAMPLE = _CRD.with_name("sample-agentdeployment.yaml")


def _seeded():
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no Postgres: {e}")
    apply_schema(conn, _SCHEMA)
    seed(conn)
    return conn


def _cr(**spec):
    return {"apiVersion": "agentctl.dev/v1alpha1", "kind": "AgentDeployment",
            "metadata": {"name": "t"}, "spec": spec}


def test_reconcile_canary():
    conn = _seeded()
    try:
        status = reconcile_agentdeployment(conn, _cr(commit=SHA_A, weight=25), actor="t")
        assert status["phase"] == "Live" and status["mode"] == "canary"
        weights = {r["git_commit_sha"]: r["weight"] for r in live_routing(conn, DEMO_PROJECT_ID)}
        assert weights[SHA_A] == 2500
    finally:
        conn.close()


def test_reconcile_promote():
    conn = _seeded()
    try:
        status = reconcile_agentdeployment(conn, _cr(commit=SHA_A, weight=100), actor="t")
        assert status["phase"] == "Live" and status["mode"] == "promote"
    finally:
        conn.close()


def test_reconcile_gate_block_leaves_routing(tmp_path):
    """requireGatePR pointing at a regression -> Blocked, routing untouched."""
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.ingest import ingest_paired
    from agentctl.eval.runner import gate_pr
    from agentctl.storage.duckdb_store import EvalStore

    db = str(tmp_path / "eval.duckdb")
    store = EvalStore.open(db)
    ingest_paired(store, candidate_path="demo/fixtures/candidate_regression.jsonl",
                  baseline_path="demo/fixtures/main.jsonl", commit_sha=SHA_A, baseline_sha="main",
                  pr_number=42)
    gate_pr(store, 42, GateConfig(nim=0.50, n_min=5))
    store.close()

    conn = _seeded()
    try:
        before = live_routing(conn, DEMO_PROJECT_ID)
        cr = _cr(commit=SHA_A, weight=100, requireGatePR=42)
        cr["spec"]["nim"] = 0.50
        # gate_db is the default store; point the reconcile at our temp db via the rollout path
        from agentctl.rollback import rollout
        orig = rollout.gated_rollout
        rollout.gated_rollout = lambda *a, **k: orig(*a, **{**k, "gate_db": db, "n_min": 5})
        try:
            status = reconcile_agentdeployment(conn, cr, actor="t")
        finally:
            rollout.gated_rollout = orig
        assert status["phase"] == "Blocked" and status["gate"] == "BLOCK"
        assert live_routing(conn, DEMO_PROJECT_ID) == before
    finally:
        conn.close()


def test_reconcile_rejects_wrong_kind_and_missing_commit():
    conn = _seeded()
    try:
        with pytest.raises(ValueError):
            reconcile_agentdeployment(conn, {"kind": "Pod", "spec": {"commit": "x"}})
        with pytest.raises(ValueError):
            reconcile_agentdeployment(conn, _cr(weight=50))   # no commit
    finally:
        conn.close()


def test_crd_and_sample_are_valid_yaml():
    yaml = pytest.importorskip("yaml")
    crd = yaml.safe_load(_CRD.read_text())
    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["spec"]["names"]["kind"] == "AgentDeployment"
    props = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
    assert "commit" in props and "weight" in props and "requireGatePR" in props

    sample = yaml.safe_load(_SAMPLE.read_text())
    assert sample["kind"] == "AgentDeployment" and "commit" in sample["spec"]
