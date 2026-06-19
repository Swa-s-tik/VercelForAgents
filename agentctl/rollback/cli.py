"""`agentctl rollback ...` subcommands (Vertical C).

All commands resolve a Principal (via --api-key / AGENTCTL_API_KEY; absent -> bootstrap owner) and
enforce a minimum role: schema/seed=owner, run=admin, routing/audit=viewer. The project is the
authenticated principal's project (the bootstrap key maps to the demo project).
"""
from __future__ import annotations

import os
import sys


def _principal(args, conn, min_role: str):
    """Resolve + authorize the caller, or print 'denied' and return None."""
    from agentctl.auth.principal import AuthError, resolve_principal
    key = getattr(args, "api_key", None) or os.environ.get("AGENTCTL_API_KEY")
    try:
        return resolve_principal(conn, key).require(min_role)
    except AuthError as e:
        print(f"denied: {e}", file=sys.stderr)
        return None


def _cmd_schema(args) -> int:
    from pathlib import Path

    import agentctl.rollback as _pkg
    from agentctl.common.db import apply_schema, connect
    from agentctl.config import STATE_BACKEND
    sql = str(Path(_pkg.__file__).with_name("schema_postgres.sql"))
    conn = connect()
    # owner-only, but check against the bootstrap principal (no DB read) since the tables may not
    # exist yet on a first apply.
    if _principal(args, conn, "owner") is None:
        conn.close()
        return 1
    n = apply_schema(conn, sql)
    msg = f"applied schema ({n} statements) to controlplane"
    if STATE_BACKEND == "pgvector":
        vsql = str(Path(_pkg.__file__).parent / "stores" / "schema_vector.sql")
        nv = apply_schema(conn, vsql)
        msg += f"; pgvector state schema ({nv} statements) to vectorstore/memorystore"
    conn.close()
    print(msg)
    return 0


def _cmd_seed(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.routing import live_routing
    from agentctl.rollback.seed import seed
    conn = connect()
    if _principal(args, conn, "owner") is None:
        conn.close()
        return 1
    info = seed(conn)
    print(f"seeded project {info['project_id']}")
    print(f"  A={info['A']['sha']} (schema v36, vector v36, memory 1000)")
    print(f"  B={info['B']['sha']} (schema v37, vector v37, memory 1180, +Stripe charge)  [LIVE]")
    print("  live routing:", [(r["git_commit_sha"], r["weight"]) for r in live_routing(conn, info["project_id"])])
    conn.close()
    return 0


def _cmd_run(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.rollback import rollback_to_commit
    from agentctl.rollback.routing import live_routing
    conn = connect()
    principal = _principal(args, conn, "admin")
    if principal is None:
        conn.close()
        return 1
    res = rollback_to_commit(conn, principal.project_id, args.sha, actor=f"cli:{principal.name}")
    print(f"rollback #{res['rollback_id']} -> {args.sha}: status={res['status']} (routing v{res['routing_version']})")
    print("  live routing now:", [(r["git_commit_sha"], r["weight"]) for r in live_routing(conn, principal.project_id)])
    if res["unrollbackable"]:
        print("  un-rollback-able (surfaced, not hidden):")
        for u in res["unrollbackable"]:
            print(f"    - [{u['class']}] {u['store_id']}: {u['reason']}")
    conn.close()
    return 0


def _cmd_rollout(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.rollout import gated_rollout, set_canary
    conn = connect()
    principal = _principal(args, conn, "developer")
    if principal is None:
        conn.close()
        return 1
    actor = f"cli:{principal.name}"
    try:
        if args.require_gate is not None:
            # interlock: roll out only if the PR's eval gate ALLOWs - a regression can't be promoted.
            verdict, res = gated_rollout(conn, principal.project_id, args.sha, args.weight,
                                         gate_pr=args.require_gate, gate_db=args.gate_db,
                                         nim=args.nim, n_min=args.n_min, actor=actor)
            if res is None:
                print(f"gate for PR #{args.require_gate}: {verdict.decision} - {verdict.reason}")
                print("rollout SKIPPED (the eval gate did not pass)")
                conn.close()
                return 1
            print(f"gate for PR #{args.require_gate}: ALLOW - proceeding")
        else:
            res = set_canary(conn, principal.project_id, args.sha, args.weight, actor=actor)
    except ValueError as e:
        print(f"rollout failed: {e}")
        conn.close()
        return 2
    print(f"rollout ({res['mode']}) {args.sha} -> {args.weight:g}%  (routing v{res['routing_version']})")
    for r in res["routing"]:
        tag = " [canary]" if r["is_canary"] else (" [shadow]" if r["shadow"] else "")
        print(f"  {r['commit'][:12]}  {r['weight'] / 100:g}%{tag}")
    conn.close()
    return 0


def _cmd_resume(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.rollback import resume_rollback
    conn = connect()
    principal = _principal(args, conn, "admin")
    if principal is None:
        conn.close()
        return 1
    res = resume_rollback(conn, principal.project_id, rollback_id=args.id,
                          actor=f"cli:{principal.name}")
    if res is None:
        print("no in-flight rollback to resume")
    else:
        print(f"resumed rollback #{res['rollback_id']} -> {res['to_commit']}: status={res['status']}")
        for u in res["unrollbackable"]:
            print(f"    - [{u['class']}] {u['store_id']}: {u['reason']}")
    conn.close()
    return 0


def _cmd_audit(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback import audit
    conn = connect()
    if _principal(args, conn, "viewer") is None:
        conn.close()
        return 1
    with conn.cursor() as cur:
        cur.execute("SELECT id, to_commit_sha, status FROM controlplane.rollbacks ORDER BY initiated_at DESC LIMIT 1")
        rb = cur.fetchone()
    if not rb:
        print("no rollbacks recorded")
        conn.close()
        return 0
    print(f"audit trail for rollback #{rb['id']} (-> {rb['to_commit_sha']}, {rb['status']}):")
    for row in audit.fetch(conn, rb["id"]):
        print(f"  {row['occurred_at']:%H:%M:%S}  {row['action']:22s} {row['target_ref'] or ''}")
    conn.close()
    return 0


def _cmd_routing(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.routing import live_routing
    conn = connect()
    principal = _principal(args, conn, "viewer")
    if principal is None:
        conn.close()
        return 1
    rows = live_routing(conn, principal.project_id)
    print("live routing table:")
    for r in rows:
        tag = "canary" if r["is_canary"] else ("shadow" if r["shadow_target"] else "primary")
        print(f"  v{r['version']}  {r['git_commit_sha']}  weight={r['weight']}bps  [{tag}]")
    conn.close()
    return 0


def add_rollback_parser(sub) -> None:
    rb = sub.add_parser("rollback", help="stateful rollback engine (Vertical C)")
    rbsub = rb.add_subparsers(dest="rbcmd", required=True)

    def _with_key(p):
        p.add_argument("--api-key", default=None, help="caller key (else AGENTCTL_API_KEY / bootstrap)")
        return p

    _with_key(rbsub.add_parser("schema", help="apply the Postgres schema (owner)")).set_defaults(func=_cmd_schema)
    _with_key(rbsub.add_parser("seed", help="seed demo deployments A & B (owner)")).set_defaults(func=_cmd_seed)
    run = _with_key(rbsub.add_parser("run", help="1-click rollback to a commit sha (admin)"))
    run.add_argument("sha")
    run.set_defaults(func=_cmd_run)
    res = _with_key(rbsub.add_parser("resume", help="re-drive a rollback that crashed mid-realignment (admin)"))
    res.add_argument("--id", type=int, default=None, help="specific rollback id (else the latest in-flight)")
    res.set_defaults(func=_cmd_resume)
    ro = _with_key(rbsub.add_parser("rollout", help="progressive forward rollout: canary % or full promote (developer)"))
    ro.add_argument("sha", help="deployment commit to shift traffic toward")
    ro.add_argument("--weight", type=float, default=100.0, help="percent of live traffic (100 = full promote)")
    ro.add_argument("--require-gate", type=int, default=None, dest="require_gate",
                    help="PR number: roll out ONLY if that PR's eval gate ALLOWs (safety interlock)")
    ro.add_argument("--gate-db", default=os.environ.get("AGENTCTL_DUCKDB", ".agentctl/eval.duckdb"),
                    dest="gate_db", help="DuckDB eval store for --require-gate")
    ro.add_argument("--nim", type=float, default=0.50, help="non-inferiority margin for --require-gate")
    ro.add_argument("--n-min", type=int, default=30, dest="n_min", help="min samples for --require-gate")
    ro.set_defaults(func=_cmd_rollout)
    _with_key(rbsub.add_parser("audit", help="show the latest rollback's audit trail (viewer)")).set_defaults(func=_cmd_audit)
    _with_key(rbsub.add_parser("routing", help="show the live routing table (viewer)")).set_defaults(func=_cmd_routing)
