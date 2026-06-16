"""`agentctl agent | gateway | ws-bridge` subcommands (Vertical B). These run long-lived
servers; the self-contained demos live in demo/gateway_demo.py and demo/ws_demo.py."""
from __future__ import annotations

import asyncio


def _cmd_agent(args) -> int:
    if getattr(args, "kind", "echo") == "support":
        from agentctl.agents.support_agent import serve_forever
    else:
        from agentctl.agents.echo_agent import serve_forever
    asyncio.run(serve_forever(args.tag, args.port))
    return 0


def _cmd_gateway(args) -> int:
    if args.engine == "python":
        from agentctl.gateway.proxy import serve_forever
        asyncio.run(serve_forever(args.port))
        return 0
    # default: the compiled Go data plane (Postgres-routed; the cutover)
    import os
    import subprocess
    import sys
    from agentctl.config import DEMO_PROJECT_ID, PG_DSN
    from agentctl.gateway.go_launcher import GATEWAY_BIN, binary_available
    if not binary_available():
        print(f"Go gateway not built: {GATEWAY_BIN}\n"
              f"  build it:  cd agentctl/gateway_core && make build\n"
              f"  or use:    agentctl gateway --engine python", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env.update({"AGENTCTL_PG_DSN": PG_DSN, "AGENTCTL_PROJECT_ID": DEMO_PROJECT_ID,
                "AGENTCTL_GW_PORT": str(args.port)})
    print(f"agentctl gateway: launching Go data plane on :{args.port} (Postgres-routed)")
    return subprocess.call([str(GATEWAY_BIN)], env=env)


def _cmd_ws(args) -> int:
    from agentctl.edge.ws_bridge import serve_forever
    asyncio.run(serve_forever(args.ws_port, args.gateway))
    return 0


def add_gateway_parsers(sub) -> None:
    ag = sub.add_parser("agent", help="run an agent backend: echo | support (Vertical B)")
    ag.add_argument("--kind", choices=["echo", "support"], default="echo")
    ag.add_argument("--tag", default="vA")
    ag.add_argument("--port", type=int, default=50051)
    ag.set_defaults(func=_cmd_agent)

    gw = sub.add_parser("gateway", help="run the streaming gateway (default: compiled Go data plane)")
    gw.add_argument("--port", type=int, default=50050)
    gw.add_argument("--engine", choices=["go", "python"], default="go",
                    help="data-plane engine (default: Go gateway_core; 'python' = grpc.aio proxy)")
    gw.set_defaults(func=_cmd_gateway)

    ws = sub.add_parser("ws-bridge", help="run the WebSocket->gRPC edge bridge (Vertical B)")
    ws.add_argument("--ws-port", type=int, default=8765, dest="ws_port")
    ws.add_argument("--gateway", default="localhost:50050")
    ws.set_defaults(func=_cmd_ws)
