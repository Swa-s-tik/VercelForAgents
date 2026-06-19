"""Unified `agentctl` command line. Subcommands are added per vertical:

  push          - package an agent dir -> preview -> live eval-gate -> merge/block (Phase 3)
  eval ingest   - ingest paired candidate/baseline eval records into DuckDB   (Vertical A)
  gate          - compute the statistical merge gate for a run or a whole PR  (Vertical A)
  rollback      - apply schema / seed / 1-click rollback / show-audit         (Vertical C)
  gateway       - run the gRPC streaming gateway                             (Vertical B)
  agent         - run an echo agent backend                                  (Vertical B)
  webhook       - git webhook emulator                                       (Phase 2)

Handlers lazy-import their vertical so an unrelated subcommand never pays for
(or fails on) another vertical's dependencies. The production-grade `push` UX lives in
``agentctl/cli/main.py`` (typer + rich); this package keeps the argparse entry so the
``agentctl`` console script and ``python -m agentctl.cli`` continue to work unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_DB = os.environ.get("AGENTCTL_DUCKDB", ".agentctl/eval.duckdb")


# --------------------------------------------------------------------------- #
# Phase 3 - push (developer experience)
# --------------------------------------------------------------------------- #
def _cmd_push(args) -> int:
    from agentctl.cli.main import run_push
    return run_push(path=args.path, simulate_regression=args.simulate_regression,
                    samples=args.samples, db=args.db, provision=not args.no_provision,
                    api_key=args.api_key)


def _add_push_parser(sub) -> None:
    p = sub.add_parser("push", help="package + deploy an agent: preview -> eval-gate -> merge/block")
    p.add_argument("path", nargs="?", default=".", help="agent directory (contains prompt.yaml)")
    p.add_argument("--simulate-regression", action="store_true",
                   help="simulate a mathematically inferior agent (-> PR BLOCKED)")
    p.add_argument("--samples", type=int, default=None, help="override eval sample count")
    p.add_argument("--no-provision", action="store_true",
                   help="skip spinning up the isolated preview container")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--api-key", default=os.environ.get("AGENTCTL_API_KEY"),
                   help="API key for the target project (else AGENTCTL_API_KEY / bootstrap)")
    p.set_defaults(func=_cmd_push)


# --------------------------------------------------------------------------- #
# Vertical A - eval / gate
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


class _Verdict:
    """Minimal verdict shape for the GitHub comment builder (a single-run gate has no PR verdict)."""
    def __init__(self, decision: str, reason: str):
        self.decision, self.reason = decision, reason


def _maybe_post_github(args, decision: str, reason: str, exit_code: int, decisions: dict) -> None:
    """--dry-run prints the would-post status + comment; --github posts them via GitHub Actions env.
    Off-CI (no GITHUB_* env) --github is a no-op with a clear note, so it's safe to always pass in CI."""
    if not (getattr(args, "github", False) or getattr(args, "dry_run", False)):
        return
    from agentctl.gitops import github_gate as gh

    target = gh.from_github_env()
    sha = target.sha if target else ""
    payload = gh.status_payload(decision, reason, exit_code, target_url=getattr(args, "target_url", "") or "")
    body = gh.comment_markdown(_Verdict(decision, reason), decisions, sha=sha, margin=args.nim)

    if getattr(args, "dry_run", False):
        print("\n--- [dry-run] commit status (POST /repos/{repo}/statuses/{sha}) ---")
        print(json.dumps(payload, indent=2))
        print("\n--- [dry-run] PR comment ---\n" + body)
        return
    if target is None:
        print("--github: no GitHub env (need GITHUB_REPOSITORY + token + SHA); skipping post",
              file=sys.stderr)
        return
    # The gate VERDICT is the source of truth for pass/fail (already in the return code); a GitHub API
    # failure (a fork's read-only token, a permissions gap) must not crash the gate or mask its result.
    try:
        code = gh.post_commit_status(target, payload)
        print(f"posted commit status [{payload['state']}] -> {target.repo}@{target.sha[:12]} (HTTP {code})")
        if target.pr is not None:
            c = gh.post_pr_comment(target, body)
            print(f"posted PR comment -> #{target.pr} (HTTP {c})")
    except Exception as e:  # noqa: BLE001 - posting is best-effort; the verdict still gates
        print(f"--github: could not post to GitHub ({e}); the gate verdict still stands", file=sys.stderr)


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
        print(f"PR #{args.pr} VERDICT: {verdict.decision}  - {verdict.reason}")
        print(f"  BH-significant per suite: {verdict.bh_significant}")
        _maybe_post_github(args, verdict.decision, verdict.reason, verdict.exit_code, decisions)
        return verdict.exit_code

    if args.run_id is not None:
        meta = store.run_meta(args.run_id)
        if meta is None:
            print(f"unknown run_id {args.run_id!r}", file=sys.stderr)
            return 2
        d = gate_run(store, args.run_id, cfg)
        print(format_decision(meta["suite_name"], d))
        exit_code = 1 if d.decision == "BLOCK" else 0
        _maybe_post_github(args, d.decision, d.reason, exit_code, {meta["suite_name"]: d})
        return exit_code

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
    g.add_argument("--github", action="store_true",
                   help="post the verdict as a GitHub commit status (+ PR comment) using Actions env")
    g.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="print the would-post status + comment instead of calling the GitHub API")
    g.add_argument("--target-url", dest="target_url",
                   default=os.environ.get("AGENTCTL_GATE_TARGET_URL", ""),
                   help="URL the status check links to (e.g. the CI run)")
    g.add_argument("--db", default=DEFAULT_DB)
    g.set_defaults(func=_cmd_gate)


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentctl",
                                description="Unified GitOps control plane for AI agents.")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_push_parser(sub)
    _add_eval_parsers(sub)
    for mod, fn in (("agentctl.rollback.cli", "add_rollback_parser"),
                    ("agentctl.gateway.cli", "add_gateway_parsers"),
                    ("agentctl.control.cli", "add_webhook_parsers"),
                    ("agentctl.auth.cli", "add_auth_parsers")):
        try:
            __import__(mod, fromlist=[fn])
            getattr(sys.modules[mod], fn)(sub)
        except Exception:
            pass
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
