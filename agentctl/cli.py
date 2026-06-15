"""Unified `agentctl` command line. Subcommands are added per vertical:

  eval ingest   — ingest paired candidate/baseline eval records into DuckDB   (Vertical A)
  gate          — compute the statistical merge gate for a run or a whole PR  (Vertical A)
  rollback      — apply schema / seed / 1-click rollback / show-audit         (Vertical C)
  gateway       — run the gRPC streaming gateway                             (Vertical B)
  agent         — run an echo agent backend                                  (Vertical B)

Handlers lazy-import their vertical so an unrelated subcommand never pays for
(or fails on) another vertical's dependencies.
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_DB = os.environ.get("AGENTCTL_DUCKDB", ".agentctl/eval.duckdb")


# --------------------------------------------------------------------------- #
# Vertical A — eval / gate
# --------------------------------------------------------------------------- #
def _cmd_eval_ingest(args) -> int:
    from agentctl.eval.ingest import ingest_paired
    from agentctl.storage.duckdb_store import EvalStore

    store = EvalStore.open(args.db)
    runs = ingest_paired(
        store, candidate_path=args.run, baseline_path=args.baseline,
        commit_sha=args.commit, baseline_sha=args.baseline_sha, pr_number=args.pr,
    )
    print(f"ingested {len(runs)} suite(s): {', '.join(runs)}")
    return 0


def _cmd_gate(args) -> int:
    from agentctl.eval.gate import GateConfig
    from agentctl.eval.runner import format_decision, gate_pr, gate_run
    from agentctl.storage.duckdb_store import EvalStore

    store = EvalStore.open(args.db)
    cfg = GateConfig(nim=args.nim, n_min=args.n_min,
                     inconclusive_action="block" if args.strict else "warn")

    if args.pr is not None:
        verdict, decisions = gate_pr(store, args.pr, cfg)
        for suite, d in decisions.items():
            print(format_decision(suite, d) + "\n")
        print(f"PR #{args.pr} VERDICT: {verdict.decision}  — {verdict.reason}")
        print(f"  BH-significant per suite: {verdict.bh_significant}")
        return verdict.exit_code

    if args.run_id is not None:
        meta = store.run_meta(args.run_id)
        if meta is None:
            print(f"unknown run_id {args.run_id!r}", file=sys.stderr)
            return 2
        d = gate_run(store, args.run_id, cfg)
        print(format_decision(meta["suite_name"], d))
        return 1 if d.decision == "BLOCK" else 0

    print("specify --run-id <id> or --pr <n>", file=sys.stderr)
    return 2


def _add_eval_parsers(sub) -> None:
    ev = sub.add_parser("eval", help="evaluation ingest (Vertical A)")
    evsub = ev.add_subparsers(dest="evalcmd", required=True)
    ing = evsub.add_parser("ingest", help="ingest paired candidate/baseline JSONL into DuckDB")
    ing.add_argument("--run", required=True, help="candidate JSONL")
    ing.add_argument("--baseline", required=True, help="baseline (main) JSONL")
    ing.add_argument("--commit", default="candidate", help="candidate commit sha")
    ing.add_argument("--baseline-sha", default="main", dest="baseline_sha")
    ing.add_argument("--pr", type=int, default=None, help="PR number (groups suites)")
    ing.add_argument("--db", default=DEFAULT_DB)
    ing.set_defaults(func=_cmd_eval_ingest)

    g = sub.add_parser("gate", help="compute the merge gate (Vertical A)")
    g.add_argument("--run-id", default=None, help="gate a single run")
    g.add_argument("--pr", type=int, default=None, help="gate a whole PR (aggregate)")
    g.add_argument("--nim", type=float, default=0.50, help="non-inferiority margin (0.52 = superiority)")
    g.add_argument("--n-min", type=int, default=100, dest="n_min")
    g.add_argument("--strict", action="store_true", help="block on INCONCLUSIVE suites")
    g.add_argument("--db", default=DEFAULT_DB)
    g.set_defaults(func=_cmd_gate)


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentctl",
                                description="Unified GitOps control plane for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_eval_parsers(sub)
    # Vertical C and B parsers are registered here as they are built:
    try:
        from agentctl.rollback.cli import add_rollback_parser
        add_rollback_parser(sub)
    except Exception:
        pass
    try:
        from agentctl.gateway.cli import add_gateway_parsers
        add_gateway_parsers(sub)
    except Exception:
        pass
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
