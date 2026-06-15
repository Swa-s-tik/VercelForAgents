"""`agentctl agent | gateway | ws-bridge` subcommands (Vertical B). These run long-lived
servers; the self-contained demos live in demo/gateway_demo.py and demo/ws_demo.py."""
from __future__ import annotations

import asyncio


def _cmd_agent(args) -> int:
    from agentctl.agents.echo_agent import serve_forever
    asyncio.run(serve_forever(args.tag, args.port))
    return 0


def _cmd_gateway(args) -> int:
    from agentctl.gateway.proxy import serve_forever
    asyncio.run(serve_forever(args.port))
    return 0


def _cmd_ws(args) -> int:
    from agentctl.edge.ws_bridge import serve_forever
    asyncio.run(serve_forever(args.ws_port, args.gateway))
    return 0


def add_gateway_parsers(sub) -> None:
    ag = sub.add_parser("agent", help="run an echo agent backend (Vertical B)")
    ag.add_argument("--tag", default="vA")
    ag.add_argument("--port", type=int, default=50051)
    ag.set_defaults(func=_cmd_agent)

    gw = sub.add_parser("gateway", help="run the gRPC streaming gateway (Vertical B)")
    gw.add_argument("--port", type=int, default=50050)
    gw.set_defaults(func=_cmd_gateway)

    ws = sub.add_parser("ws-bridge", help="run the WebSocket->gRPC edge bridge (Vertical B)")
    ws.add_argument("--ws-port", type=int, default=8765, dest="ws_port")
    ws.add_argument("--gateway", default="localhost:50050")
    ws.set_defaults(func=_cmd_ws)
