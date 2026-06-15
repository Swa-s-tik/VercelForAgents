"""Thin FastAPI surface mirroring the eval CLI — the seam the gRPC gateway / CI calls.

Run: uvicorn agentctl.eval.api:app --port 8089
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException

from agentctl.eval.gate import GateConfig
from agentctl.eval.runner import gate_pr, gate_run
from agentctl.storage.duckdb_store import EvalStore

app = FastAPI(title="agentctl eval-gating")


def _store() -> EvalStore:
    return EvalStore.open()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/gate/{run_id}")
def gate(run_id: str, nim: float = 0.50, n_min: int = 100):
    store = _store()
    if store.run_meta(run_id) is None:
        raise HTTPException(404, f"unknown run_id {run_id!r}")
    d = gate_run(store, run_id, GateConfig(nim=nim, n_min=n_min))
    return asdict(d)


@app.get("/gate/pr/{pr_number}")
def gate_pr_endpoint(pr_number: int, nim: float = 0.50, n_min: int = 100):
    store = _store()
    verdict, decisions = gate_pr(store, pr_number, GateConfig(nim=nim, n_min=n_min))
    return {
        "pr": pr_number,
        "verdict": verdict.decision,
        "reason": verdict.reason,
        "blocking_suites": verdict.blocking_suites,
        "bh_significant": verdict.bh_significant,
        "suites": {s: asdict(d) for s, d in decisions.items()},
    }
