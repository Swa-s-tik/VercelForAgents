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
    def __init__(self, router: Router | None = None, channel_options=None, tracer=None):
        self.router = router or Router(RouteCache())
        self._channel_options = channel_options
        self._tracer = tracer          # optional OTel tracer (Phase 5); None = zero overhead
        self._channels: dict[str, grpc.aio.Channel] = {}
        self.stats = {"sessions": 0, "by_arm": {}, "shadow_sent": 0,
                      "shadow_dropped": 0, "shadow_received": 0}

    def _stub(self, endpoint: str):
        ch = self._channels.get(endpoint)
        if ch is None:
            ch = grpc.aio.insecure_channel(endpoint, options=self._channel_options)  # pooled per backend
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
        n_out = 0
        try:
            async for resp in primary:
                resp.attributes["canary_arm"] = decision.arm
                n_out += 1
                yield resp
        finally:
            await pump_task
            self.stats["shadow_sent"] += sum(s.sent for s in shadows)
            self.stats["shadow_dropped"] += sum(s.dropped for s in shadows)
            self.stats["shadow_received"] += sum(s.received for s in shadows)
            if self._tracer is not None:
                try:
                    from agentctl.telemetry.exporter import record_stream_metrics
                    record_stream_metrics(
                        self._tracer, session_id=session_id, canary_arm=decision.arm,
                        measures={"frames_out": float(n_out),
                                  "shadow_sent": float(sum(s.sent for s in shadows)),
                                  "shadow_dropped": float(sum(s.dropped for s in shadows))})
                except Exception:
                    pass

    async def Health(self, request, context):
        return dp.HealthReply(ready=True, version_tag="gateway")


async def serve(port: int, servicer: GatewayServicer | None = None,
                options=None) -> tuple[grpc.aio.Server, GatewayServicer]:
    servicer = servicer or GatewayServicer()
    # API-key interceptor: permissive by default (no key passes through), enforces when a key is
    # present or AGENTCTL_REQUIRE_KEY=1 — so existing tests/demo run keyless and unchanged.
    from agentctl.auth.grpc_interceptor import ApiKeyServerInterceptor
    server = grpc.aio.server(options=options, interceptors=[ApiKeyServerInterceptor()])
    dpg.add_AgentStreamServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server, servicer


async def serve_forever(port: int) -> None:
    server, _ = await serve(port)
    print(f"gateway listening on :{port}")
    await server.wait_for_termination()
