"""Self-contained Vertical B demo over REAL gRPC (localhost, in one process):
  * 3 echo agents (vA / vB / shadow) + the gateway
  * 100 sessions -> ~90/10 sticky canary split + shadow mirroring (responses discarded)
  * a mid-stream INTERRUPT that cuts generation short
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter

os.environ.setdefault("GRPC_VERBOSITY", "NONE")   # quiet gRPC's noisy shutdown logs

import grpc

from agentctl.agents.echo_agent import serve as serve_agent
from agentctl.gateway import frames as F
from agentctl.gateway.proxy import serve as serve_gateway
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


async def canary_session(stub, i: int) -> str:
    async def gen():
        yield F.client_text(f"session-{i}", 1, 0, f"hello {i}", attrs={"tokens": 1})
    arm = "?"
    async for resp in stub.Converse(gen()):
        arm = resp.attributes.get("canary_arm", arm)
    return arm


async def interrupt_session(stub) -> tuple[int, int | None]:
    async def gen():
        yield F.client_text("session-irq", 1, 0, "write a long answer",
                            attrs={"tokens": 30, "delay": 0.03})
        await asyncio.sleep(0.12)                    # let a few tokens stream...
        yield F.client_control("session-irq", 1, 1, pb.INTERRUPT)   # ...then barge in
    tokens, final = 0, None
    async for resp in stub.Converse(gen()):
        if resp.HasField("text"):
            tokens += 1
        elif resp.HasField("turn_end"):
            final = int(resp.turn_end.reason)
    return tokens, final


async def main():
    agents = [await serve_agent("vA", 50051), await serve_agent("vB", 50052),
              await serve_agent("shadow", 50053)]
    gw_server, gw = await serve_gateway(50050)
    channel = grpc.aio.insecure_channel("localhost:50050")
    stub = dpg.AgentStreamStub(channel)
    try:
        # --- canary + shadow ---
        dist = Counter([await canary_session(stub, i) for i in range(100)])
        print(f"canary distribution over 100 sessions: {dict(dist)}")
        print(f"gateway stats: sessions={gw.stats['sessions']} by_arm={gw.stats['by_arm']}")
        print(f"shadow: sent={gw.stats['shadow_sent']} received(discarded)={gw.stats['shadow_received']} "
              f"dropped={gw.stats['shadow_dropped']}")
        assert set(dist) <= {"vA", "vB"} and dist["vA"] > dist["vB"], dist
        assert gw.stats["shadow_sent"] > 0, "shadow never received mirrored traffic"

        # --- interrupt ---
        tokens, final = await interrupt_session(stub)
        reason = pb.FinishReason.Name(final) if final is not None else "NONE"
        print(f"interrupt: streamed {tokens} token(s) before barge-in, final TurnEnd reason={reason}")
        assert final == pb.INTERRUPTED, f"expected INTERRUPTED, got {reason}"
        print("\nVertical B demo: PASS")
    finally:
        await channel.close()
        await gw_server.stop(0)
        for a in agents:
            await a.stop(0)


if __name__ == "__main__":
    asyncio.run(main())
