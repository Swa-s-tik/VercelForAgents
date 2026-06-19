"""Drive the gate over stored eval runs and persist verdicts (Vertical A orchestrator).

In production this also provisions the preview agent, posts the PR status check, and
writes the verdict back to Postgres ``eval_gates``. Those integrations are stubbed here;
the statistical decision path is fully real.
"""
from __future__ import annotations

from agentctl.eval.gate import GateConfig, GateDecision, PRVerdict, aggregate_pr, evaluate_gate
from agentctl.storage.duckdb_store import EvalStore


def gate_run(store: EvalStore, run_id: str, cfg: GateConfig = GateConfig()) -> GateDecision:
    """Roll up one run's samples and compute + persist its gate decision."""
    wins, losses, ties = store.fetch_aggregate(run_id)
    decision = evaluate_gate(wins, losses, ties, cfg)
    store.save_gate_result(run_id, decision)
    return decision


def gate_pr(
    store: EvalStore,
    pr_number: int,
    cfg: GateConfig = GateConfig(),
    gating_suites: set[str] | None = None,
) -> tuple[PRVerdict, dict[str, GateDecision]]:
    """Gate every suite of a PR and aggregate into one verdict (with BH-FDR context)."""
    decisions: dict[str, GateDecision] = {}
    for run_id, suite in store.runs_for_pr(pr_number):
        decisions[suite] = gate_run(store, run_id, cfg)
    verdict = aggregate_pr(decisions, gating_suites=gating_suites)
    return verdict, decisions


def format_decision(suite: str, d: GateDecision) -> str:
    return (
        f"suite={suite:14s} n={d.n:<5d} wins={d.wins} losses={d.losses} ties={d.ties} "
        f"win_rate={d.win_rate:.3f}\n"
        f"  Wilson95 = [{d.wilson_low:.3f}, {d.wilson_high:.3f}]   margin(nim)={d.margin:.2f}\n"
        f"  McNemar p = {d.p_value:.3f} (paired)   Bayes P(theta>nim) = {d.bayes_p_better:.3f}\n"
        f"  DECISION: {d.decision}  - {d.reason}"
    )
