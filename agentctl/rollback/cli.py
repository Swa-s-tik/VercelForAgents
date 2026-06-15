"""`agentctl rollback ...` subcommands (Vertical C)."""
from __future__ import annotations

from agentctl.config import DEMO_PROJECT_ID


def _cmd_schema(args) -> int:
    from agentctl.common.db import apply_schema, connect
    import agentctl.rollback as _pkg
    from pathlib import Path
    sql = str(Path(_pkg.__file__).with_name("schema_postgres.sql"))
    conn = connect()
    n = apply_schema(conn, sql)
    conn.close()
    print(f"applied schema ({n} statements) to controlplane")
    return 0


def _cmd_seed(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.routing import live_routing
    from agentctl.rollback.seed import seed
    conn = connect()
    info = seed(conn)
    print(f"seeded project {info['project_id']}")
    print(f"  A={info['A']['sha']} (schema v36, vector v36, memory 1000)")
    print(f"  B={info['B']['sha']} (schema v37, vector v37, memory 1180, +Stripe charge)  [LIVE]")
    print("  live routing:", [(r["git_commit_sha"], r["weight"]) for r in live_routing(conn, DEMO_PROJECT_ID)])
    conn.close()
    return 0


def _cmd_run(args) -> int:
    from agentctl.common.db import connect
    from agentctl.rollback.rollback import rollback_to_commit
    from agentctl.rollback.routing import live_routing
    conn = connect()
    res = rollback_to_commit(conn, DEMO_PROJECT_ID, args.sha, actor="cli")
    print(f"rollback #{res['rollback_id']} -> {args.sha}: status={res['status']} (routing v{res['routing_version']})")
    print("  live routing now:", [(r["git_commit_sha"], r["weight"]) for r in live_routing(conn, DEMO_PROJECT_ID)])
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
    with conn.cursor() as cur:
        cur.execute("SELECT id, to_commit_sha, status FROM controlplane.rollbacks ORDER BY initiated_at DESC LIMIT 1")
        rb = cur.fetchone()
    if not rb:
        print("no rollbacks recorded")
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
    rows = live_routing(conn, DEMO_PROJECT_ID)
    print("live routing table:")
    for r in rows:
        tag = "canary" if r["is_canary"] else ("shadow" if r["shadow_target"] else "primary")
        print(f"  v{r['version']}  {r['git_commit_sha']}  weight={r['weight']}bps  [{tag}]")
    conn.close()
    return 0


def add_rollback_parser(sub) -> None:
    rb = sub.add_parser("rollback", help="stateful rollback engine (Vertical C)")
    rbsub = rb.add_subparsers(dest="rbcmd", required=True)
    rbsub.add_parser("schema", help="apply the Postgres schema (drops existing)").set_defaults(func=_cmd_schema)
    rbsub.add_parser("seed", help="seed demo deployments A & B, checkpoints, B live").set_defaults(func=_cmd_seed)
    run = rbsub.add_parser("run", help="1-click rollback to a commit sha")
    run.add_argument("sha")
    run.set_defaults(func=_cmd_run)
    rbsub.add_parser("audit", help="show the latest rollback's audit trail").set_defaults(func=_cmd_audit)
    rbsub.add_parser("routing", help="show the live routing table").set_defaults(func=_cmd_routing)
