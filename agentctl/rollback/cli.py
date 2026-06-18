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
    sql = str(Path(_pkg.__file__).with_name("schema_postgres.sql"))
    conn = connect()
    # owner-only, but check against the bootstrap principal (no DB read) since the tables may not
    # exist yet on a first apply.
    if _principal(args, conn, "owner") is None:
        conn.close()
        return 1
    n = apply_schema(conn, sql)
    conn.close()
    print(f"applied schema ({n} statements) to controlplane")
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
    _with_key(rbsub.add_parser("audit", help="show the latest rollback's audit trail (viewer)")).set_defaults(func=_cmd_audit)
    _with_key(rbsub.add_parser("routing", help="show the live routing table (viewer)")).set_defaults(func=_cmd_routing)
