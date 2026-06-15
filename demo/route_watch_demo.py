"""Phase 3 demo: the gateway's PG route cache hot-swaps routing the instant Postgres changes
(via LISTEN/NOTIFY), while an active streaming session straddling the flip is never dropped."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

os.environ.setdefault("GRPC_VERBOSITY", "NONE")

import grpc

import agentctl.rollback as rb
from agentctl.agents.echo_agent import serve as serve_agent
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.gateway import frames as F
from agentctl.gateway.pg_route_cache import PgRouteCache
from agentctl.gateway.proxy import GatewayServicer, serve as serve_gateway
from agentctl.gateway.router import Router
from agentctl.gen import load
from agentctl.rollback import routing
from agentctl.rollback.seed import SHA_A, seed

pb, dp, dpg, _cp, _cpg = load()


async def one_session(stub, sid, tokens=1, delay=0.0):
    async def gen():
        yield F.client_text(sid, 1, 0, "hi", attrs={"tokens": tokens, "delay": delay})
    arm = "?"
    async for resp in stub.Converse(gen()):
        arm = resp.attributes.get("canary_arm", arm)
    return arm


async def main() -> int:
    conn = connect()
    apply_schema(conn, str(Path(rb.__file__).with_name("schema_postgres.sql")))
    seed(conn)                                   # B live; A->:50051(vA), B->:50052(vB)
    aA = await serve_agent("vA", 50051)
    aB = await serve_agent("vB", 50052)
    cache = PgRouteCache(DEMO_PROJECT_ID)
    await cache.start_async_watching()
    gw, _ = await serve_gateway(50050, GatewayServicer(Router(cache)))
    ch = grpc.aio.insecure_channel("localhost:50050")
    stub = dpg.AgentStreamStub(ch)
    try:
        print(f"initial live routing: {[b.version_tag for b in cache.snapshot().primary]} "
              f"(v{cache.snapshot().version})")
        arm_before = await one_session(stub, "s-before")
        print(f"  session before flip -> served by {arm_before}")

        # a long active stream that straddles the routing flip
        long_task = asyncio.create_task(one_session(stub, "s-long", tokens=25, delay=0.04))
        await asyncio.sleep(0.1)

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM controlplane.deployments WHERE project_id=%s AND git_commit_sha=%s",
                        [DEMO_PROJECT_ID, SHA_A])
            a_id = cur.fetchone()["id"]
        conn.commit()   # close the implicit read txn so flip_routing commits at top level (-> NOTIFY fires)
        routing.flip_routing(conn, DEMO_PROJECT_ID, a_id, reason="rollback:A", actor="demo")
        print("  >>> flipped routing -> A in Postgres (pg_notify fired inside the commit)")

        for _ in range(60):
            snap = cache.snapshot()
            if cache.reloads >= 1 and snap.primary and snap.primary[0].version_tag == "vA":
                break
            await asyncio.sleep(0.1)
        print(f"  cache reloaded {cache.reloads}x via NOTIFY -> live routing now "
              f"{[b.version_tag for b in cache.snapshot().primary]}")

        arm_after = await one_session(stub, "s-after")
        print(f"  session after flip  -> served by {arm_after}")
        long_arm = await long_task
        print(f"  long active stream (straddled the flip) -> served by {long_arm}, completed uninterrupted")

        ok = (arm_before == "vB" and arm_after == "vA" and cache.reloads >= 1 and long_arm == "vB")
        print("\nPhase 3 route-watch: " +
              ("PASS ✔ (instant NOTIFY switch, zero dropped active streams)" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        await cache.stop_async_watching()
        await ch.close()
        await gw.stop(0)
        await aA.stop(0)
        await aB.stop(0)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
