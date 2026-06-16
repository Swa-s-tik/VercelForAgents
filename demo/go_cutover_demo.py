"""Milestone 1 proof: the compiled Go data plane is routed by Postgres LISTEN/NOTIFY.

The Python control plane seeds routing (B live), launches the Go gateway binary (which LISTENs
on routing_changed), sends a client session through it, then flips routing -> A in Postgres.
The live Go gateway must pick up the flip and re-route — proving the cutover.
"""
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
from agentctl.gateway.go_launcher import binary_available, launch_go_gateway, stop
from agentctl.gen import load
from agentctl.rollback import routing
from agentctl.rollback.seed import SHA_A, seed

pb, dp, dpg, _cp, _cpg = load()


async def one_session(stub, sid):
    async def gen():
        yield F.client_text(sid, 1, 0, "hi", attrs={"tokens": 1})
    arm = "?"
    async for resp in stub.Converse(gen()):
        arm = resp.attributes.get("canary_arm", arm)
    return arm


async def main() -> int:
    if not binary_available():
        print("Go gateway binary not built — run: cd agentctl/gateway_core && make build")
        return 1
    conn = connect()
    apply_schema(conn, str(Path(rb.__file__).with_name("schema_postgres.sql")))
    seed(conn)                                   # A->:50051(vA), B->:50052(vB), B live
    aA = await serve_agent("vA", 50051)
    aB = await serve_agent("vB", 50052)
    gw = None
    try:
        gw = launch_go_gateway(port=50050, project_id=DEMO_PROJECT_ID)
        print("Go data plane launched (Postgres-routed, LISTEN routing_changed)")
        ch = grpc.aio.insecure_channel("localhost:50050")
        stub = dpg.AgentStreamStub(ch)

        arm_before = await one_session(stub, "s-before")
        print(f"  session before flip -> [{arm_before}]   (live deployment = B)")

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM controlplane.deployments WHERE project_id=%s AND git_commit_sha=%s",
                        [DEMO_PROJECT_ID, SHA_A])
            a_id = cur.fetchone()["id"]
        conn.commit()                            # close read txn so the flip's NOTIFY commits
        routing.flip_routing(conn, DEMO_PROJECT_ID, a_id, reason="rollback:A", actor="cutover")
        print("  >>> Python control plane flipped routing -> A in Postgres (pg_notify fired)")

        arm_after = "?"
        for i in range(40):
            await asyncio.sleep(0.1)
            arm_after = await one_session(stub, f"s-after-{i}")
            if arm_after == "vA":
                break
        print(f"  session after flip  -> [{arm_after}]   (Go gateway re-routed from Postgres)")
        await ch.close()

        ok = arm_before == "vB" and arm_after == "vA"
        print("\nGo data-plane cutover: " +
              ("PASS ✔ (Postgres LISTEN/NOTIFY flip updated the live Go gateway routing)"
               if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        stop(gw)
        await aA.stop(0)
        await aB.stop(0)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
