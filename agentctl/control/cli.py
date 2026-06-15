"""`agentctl webhook ...` subcommands (Phase 2)."""
from __future__ import annotations


def _cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("agentctl.control.webhook:app", host="127.0.0.1", port=args.port)
    return 0


def _cmd_simulate(args) -> int:
    from agentctl.common.db import connect
    from agentctl.control.webhook import handle_push, make_push_payload
    conn = connect()
    changed = args.changed.split(",") if args.changed else None
    res = handle_push(conn, make_push_payload(args.sha, version_tag=args.tag, changed=changed))
    print(f"registered deployment #{res['deployment_id']} sha={res['commit_sha']} "
          f"status={res['status']} sequence={' -> '.join(res['sequence'])}")
    conn.close()
    return 0


def add_webhook_parsers(sub) -> None:
    wh = sub.add_parser("webhook", help="git webhook emulator (Phase 2)")
    s = wh.add_subparsers(dest="whcmd", required=True)
    srv = s.add_parser("serve", help="run the webhook HTTP listener")
    srv.add_argument("--port", type=int, default=8088)
    srv.set_defaults(func=_cmd_serve)
    sim = s.add_parser("simulate", help="register a deployment from a simulated push")
    sim.add_argument("--sha", required=True)
    sim.add_argument("--tag", default="vP")
    sim.add_argument("--changed", default="")
    sim.set_defaults(func=_cmd_simulate)
