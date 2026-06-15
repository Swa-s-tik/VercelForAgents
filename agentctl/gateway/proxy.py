"""The streaming reverse-proxy core (Vertical B).

Per session: resolve a sticky canary arm, open a bidi Converse to the primary backend,
fan inbound client frames to the primary (lossless, backpressure-propagating) and OFFER
them to shadow backends (lossy, drop-on-full), and stream primary responses back. Shadow
responses are discarded. The gateway never parses token text — it routes, splits, mirrors.
"""
from __future__ import annotations

import asyncio

import grpc

from agentctl.gateway import frames as F
from agentctl.gateway.route_cache import RouteCache
from agentctl.gateway.router import Router
from agentctl.gateway.shadow import ShadowChannel
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


class GatewayServicer(dpg.AgentStreamServicer):
    def __init__(self, router: Router | None = None):
        self.router = router or Router(RouteCache())
        self._channels: dict[str, grpc.aio.Channel] = {}
        self.stats = {"sessions": 0, "by_arm": {}, "shadow_sent": 0,
                      "shadow_dropped": 0, "shadow_received": 0}

    def _stub(self, endpoint: str):
        ch = self._channels.get(endpoint)
        if ch is None:
            ch = grpc.aio.insecure_channel(endpoint)   # one pooled channel per backend
            self._channels[endpoint] = ch
        return dpg.AgentStreamStub(ch)

    async def Converse(self, request_iterator, context):
        it = request_iterator.__aiter__()
        try:
            first = await it.__anext__()
        except StopAsyncIteration:
            return

        session_id = first.session_id or "anon"
        decision = self.router.resolve(session_id)        # sticky: chosen once per session
        self.stats["sessions"] += 1
        self.stats["by_arm"][decision.arm] = self.stats["by_arm"].get(decision.arm, 0) + 1

        primary = self._stub(decision.primary.endpoint).Converse()
        shadows = [ShadowChannel(b.version_tag, self._stub(b.endpoint).Converse())
                   for b in decision.shadows]

        async def pump():
            # primary: awaited write (lossless). shadows: offer (lossy, never blocks).
            await primary.write(first)
            for s in shadows:
                s.offer(F.shadow_copy(first))
            async for frame in it:
                await primary.write(frame)
                for s in shadows:
                    s.offer(F.shadow_copy(frame))
            await primary.done_writing()
            for s in shadows:
                await s.close()

        pump_task = asyncio.create_task(pump())
        try:
            async for resp in primary:
                resp.attributes["canary_arm"] = decision.arm
                yield resp
        finally:
            await pump_task
            self.stats["shadow_sent"] += sum(s.sent for s in shadows)
            self.stats["shadow_dropped"] += sum(s.dropped for s in shadows)
            self.stats["shadow_received"] += sum(s.received for s in shadows)

    async def Health(self, request, context):
        return dp.HealthReply(ready=True, version_tag="gateway")


async def serve(port: int, servicer: GatewayServicer | None = None) -> tuple[grpc.aio.Server, GatewayServicer]:
    servicer = servicer or GatewayServicer()
    server = grpc.aio.server()
    dpg.add_AgentStreamServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server, servicer


async def serve_forever(port: int) -> None:
    server, _ = await serve(port)
    print(f"gateway listening on :{port}")
    await server.wait_for_termination()
