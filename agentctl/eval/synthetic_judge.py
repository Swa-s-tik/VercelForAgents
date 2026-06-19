"""Synthetic judge (Vertical A de-stubbing).

Simulates running an eval suite against a new "Preview Agent" by sampling PAIRED preferences
(WIN/LOSS/TIE, candidate POV) from the agent's TRUE quality vs baseline. This generates
mathematically realistic sampling variance (Bernoulli/multinomial draws), inserts the records
directly into the DuckDB OLAP store, and runs the real Wilson-CI + McNemar gate on them - no
hardcoded fixtures.

Run:
  python -m agentctl.eval.synthetic_judge --p-win 0.36 --n 240 --pr 200   # inferior  -> BLOCK
  python -m agentctl.eval.synthetic_judge --p-win 0.60 --n 240 --pr 201   # superior  -> ALLOW
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from agentctl.eval.gate import GateConfig
from agentctl.eval.runner import format_decision, gate_run
from agentctl.storage.duckdb_store import DEFAULT_DB, EvalStore, Sample


class SyntheticJudge:
    """Samples paired preferences from a true (p_win, p_tie); remaining mass is LOSS."""

    def __init__(self, p_win: float, p_tie: float, seed: int | None = None):
        if p_win + p_tie > 1.0:
            raise ValueError("p_win + p_tie must be <= 1.0")
        self.p_win, self.p_tie = p_win, p_tie
        self.rng = np.random.default_rng(seed)

    def judge_suite(self, n: int) -> list[str]:
        draws = self.rng.random(n)
        out = []
        for x in draws:
            if x < self.p_win:
                out.append("WIN")
            elif x < self.p_win + self.p_tie:
                out.append("TIE")
            else:
                out.append("LOSS")
        return out


def simulate_and_gate(store: EvalStore, *, p_win: float, p_tie: float, n: int, suite: str,
                      commit: str, baseline: str, pr: int | None, seed: int | None,
                      cfg: GateConfig):
    judge = SyntheticJudge(p_win, p_tie, seed)
    prefs = judge.judge_suite(n)
    prefix = f"pr{pr}-" if pr is not None else ""
    run_id = f"{prefix}{commit[:8]}-{suite}"
    store.create_run(run_id=run_id, commit_sha=commit, baseline_sha=baseline, suite_name=suite,
                     pr_number=pr, judge_name="SyntheticJudge",
                     judge_version=f"p_win={p_win},p_tie={p_tie},seed={seed}",
                     config=cfg.__dict__)
    samples = [Sample(item_id=f"{suite}-{i:04d}", preference=p, judge_confidence=1.0)
               for i, p in enumerate(prefs)]
    store.record_samples(run_id, samples)
    store.finish_run(run_id)
    return run_id, gate_run(store, run_id, cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="synthetic judge -> DuckDB -> eval gate")
    ap.add_argument("--p-win", type=float, required=True, dest="p_win",
                    help="true probability the preview agent wins a prompt vs baseline")
    ap.add_argument("--p-tie", type=float, default=0.06, dest="p_tie")
    ap.add_argument("--n", type=int, default=240, help="prompts in the suite")
    ap.add_argument("--suite", default="correctness")
    ap.add_argument("--commit", default="preview")
    ap.add_argument("--baseline", default="main")
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--nim", type=float, default=0.50)
    ap.add_argument("--n-min", type=int, default=100, dest="n_min")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    store = EvalStore.open(args.db)
    cfg = GateConfig(nim=args.nim, n_min=args.n_min)
    run_id, d = simulate_and_gate(
        store, p_win=args.p_win, p_tie=args.p_tie, n=args.n, suite=args.suite,
        commit=args.commit, baseline=args.baseline, pr=args.pr, seed=args.seed, cfg=cfg)

    if args.label:
        print(f"### {args.label}")
    print(f"SyntheticJudge: simulated {args.n} prompts (true p_win={args.p_win}, "
          f"p_tie={args.p_tie}) -> realized wins={d.wins} losses={d.losses} ties={d.ties} "
          f"-> DuckDB run {run_id}")
    print(format_decision(args.suite, d))
    return 1 if d.decision == "BLOCK" else 0


if __name__ == "__main__":
    sys.exit(main())
