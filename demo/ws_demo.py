"""Self-contained WebSocket-edge demo (Vertical B): browser-style WS client -> ws-bridge
-> gRPC gateway -> echo agent, demonstrating a mid-generation interrupt round-tripped
over the WebSocket edge."""
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("GRPC_VERBOSITY", "NONE")

import websockets

from agentctl.agents.echo_agent import serve as serve_agent
from agentctl.edge.ws_bridge import serve as serve_ws
from agentctl.gateway.proxy import GatewayServicer, serve as serve_gateway
from agentctl.gateway.route_cache import RouteCache
from agentctl.gateway.router import Backend, RouteTable, Router
from agentctl.gen import load

pb = load()[0]


async def main():
    agent = await serve_agent("vA", 50051)
    cache = RouteCache({"default": RouteTable("default", 1, (Backend("vA", "localhost:50051", 100, "vA"),))})
    gw_server, _ = await serve_gateway(50050, GatewayServicer(Router(cache)))
    ws_server = await serve_ws(8765, "localhost:50050")
    try:
        async with websockets.connect("ws://localhost:8765") as ws:
            await ws.send(json.dumps({"type": "text", "content": "stream me",
                                      "stream_id": 1, "tokens": 30, "delay": 0.03}))

            async def send_interrupt():
                await asyncio.sleep(0.12)
                await ws.send(json.dumps({"type": "interrupt", "stream_id": 1}))

            irq = asyncio.create_task(send_interrupt())
            tokens, final = 0, None
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                    if msg["type"] == "text":
                        tokens += 1
                    elif msg["type"] == "turn_end":
                        final = msg["reason"]
                        break
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                pass
            await irq
            reason = pb.FinishReason.Name(final) if final is not None else "NONE"
            print(f"WS edge: streamed {tokens} token(s) over WebSocket, final TurnEnd reason={reason}")
            assert final == pb.INTERRUPTED, f"expected INTERRUPTED, got {reason}"
            print("\nWS edge demo: PASS")
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        await gw_server.stop(0)
        await agent.stop(0)


if __name__ == "__main__":
    asyncio.run(main())
