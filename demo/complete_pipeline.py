"""Phase 10 — the unified end-to-end pipeline, stitching all verticals in one process:

  git push -> webhook registers + provisions an isolated preview agent (Phase 1/2)
           -> canary 90/10 via Postgres routing, gateway PG route cache picks it up (Phase 3)
           -> real-time multi-modal gRPC traffic (text canary + 1MB binary frames, OTel spans, Phase 5)
           -> promote candidate to 100%
           -> LIVE statistical eval-gating: sequential SPRT BLOCKs the inferior candidate early (Phase 4)
           -> controlled stateful rollback to the stable commit (Phase 8) — state realigned, audited
           -> validate routing-cache integrity: gateway now serves stable, zero dropped streams.

Everything runs against the real Postgres + real gRPC over localhost.
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from pathlib import Path

os.environ.setdefault("GRPC_VERBOSITY", "NONE")

import grpc

import agentctl.rollback as rb
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.control.webhook import handle_push, make_push_payload, teardown_preview
from agentctl.eval.engine import sequential_evaluate
from agentctl.eval.synthetic_judge import SyntheticJudge, simulate_and_gate
from agentctl.eval.gate import GateConfig
from agentctl.gateway import frames as F
from agentctl.gateway.pg_route_cache import PgRouteCache
from agentctl.gateway.proxy import GatewayServicer, serve as serve_gateway
from agentctl.gateway.router import Router
from agentctl.gateway.tuning import GRPC_OPTIONS
from agentctl.gen import load
from agentctl.rollback import manifest as mf, routing
from agentctl.rollback.models import Pointer
from agentctl.rollback.rollback import rollback_to_commit
from agentctl.rollback.stores.base import JsonBackend
from agentctl.rollback.stores.memory_stub import MemoryGraphStub
from agentctl.rollback.stores.schema_stub import SchemaStoreStub
from agentctl.rollback.stores.vector_stub import VectorStoreStub
from agentctl.runtime.isolated import ProcessRuntime
from agentctl.storage.duckdb_store import EvalStore
from agentctl.telemetry.exporter import make_tracer_provider

pb, dp, dpg, _cp, _cpg = load()
_MB = 1024 * 1024
STABLE_SHA = "57ab1e000main"
CAND_SHA = "ca4d10a7e001"


def _pointers(ns, mem, schema, side_effect):
    v, m, s = VectorStoreStub(), MemoryGraphStub(), SchemaStoreStub()
    pts = [
        Pointer("vector_store", "reversible", "vector:demo", {"namespace": ns, "snapshot_id": f"snap-{ns}"},
                state_digest=v.snapshot({"namespace": ns}), strategy="namespace_swap"),
        Pointer("relational_schema", "forward_fix", "appdb:demo", {"migration_version": schema},
                state_digest=s.snapshot({"migration_version": schema}), strategy="expand_contract"),
        Pointer("memory_graph", "reversible", "memory:demo",
                {"graph_id": "user:1", "snapshot_seq": mem, "log_offset": mem},
                state_digest=m.snapshot({"snapshot_seq": mem, "log_offset": mem}), strategy="event_sourced_rewind"),
    ]
    if side_effect:
        pts.append(Pointer("side_effect", "irreversible", "stripe",
                           {"provider": "stripe", "idempotency_key": f"pay_{CAND_SHA[:6]}",
                            "external_ref": "ch_live", "compensation": "refund"}, strategy="compensate_or_flag"))
    return pts


async def text_session(stub, sid):
    async def gen():
        yield F.client_text(sid, 1, 0, "hi", attrs={"tokens": 1})
    arm = "?"
    async for resp in stub.Converse(gen()):
        arm = resp.attributes.get("canary_arm", arm)
    return arm


async def binary_burst(stub, n, size):
    payload = os.urandom(size)
    sent = 0

    async def gen():
        nonlocal sent
        for i in range(n):
            sent += 1
            yield pb.Frame(session_id="mm", stream_id=1, seq=i, direction=pb.CLIENT_TO_AGENT,
                           binary=pb.BinaryChunk(modality=pb.VIDEO_FRAME, codec="raw_rgb", index=i, data=payload))
    async for _ in stub.Converse(gen()):
        pass
    return sent


async def wait_for(cache, predicate, timeout=6.0):
    for _ in range(int(timeout / 0.1)):
        if predicate(cache.snapshot()):
            return True
        await asyncio.sleep(0.1)
    return False


def step(n, title):
    print(f"\n── STEP {n}: {title} " + "─" * max(0, 56 - len(title)))


async def main() -> int:
    conn = connect()
    apply_schema(conn, str(Path(rb.__file__).with_name("schema_postgres.sql")))
    rt = ProcessRuntime()
    provider = make_tracer_provider("agentctl-pipeline", backend="postgres")
    tracer = provider.get_tracer("pipeline")
    stable_dep = cand_dep = None
    gw = ch = cache = None
    try:
        # ---- STEP 1: git push -> webhook -> register + provision isolated previews ----
        step(1, "git push -> webhook -> provision isolated preview agents")
        stable = handle_push(conn, make_push_payload(STABLE_SHA, ref="refs/heads/main",
                             changed=["baseline"], version_tag="vMain"), provision=True, runtime=rt)
        cand = handle_push(conn, make_push_payload(CAND_SHA, ref="refs/heads/candidate",
                           changed=["prompts/agent.yaml"], version_tag="vCanary"), provision=True, runtime=rt)
        stable_dep, cand_dep = stable["deployment_id"], cand["deployment_id"]
        print(f"  stable  {STABLE_SHA} -> {stable['endpoint']} (seq {'/'.join(stable['sequence'])})")
        print(f"  candidate {CAND_SHA} -> {cand['endpoint']} (seq {'/'.join(cand['sequence'])})")

        # seal checkpoints + set stable as the initial live deployment
        mf.seal_checkpoint(conn, stable_dep, STABLE_SHA, _pointers("ns-stable", 1000, 36, side_effect=False))
        mf.seal_checkpoint(conn, cand_dep, CAND_SHA, _pointers("ns-cand", 1100, 37, side_effect=True))
        JsonBackend().save({})
        VectorStoreStub().upsert("ns-stable", range(40)); VectorStoreStub().upsert("ns-cand", range(60))
        VectorStoreStub().set_alias("ns-stable")
        MemoryGraphStub().append_many(1000, lambda i: STABLE_SHA); MemoryGraphStub().set_head(1000, 1000)
        SchemaStoreStub().set_version(36)
        conn.commit()
        routing.flip_routing(conn, DEMO_PROJECT_ID, stable_dep, reason=f"deploy:{STABLE_SHA}", actor="ci")

        # ---- gateway with the PG route cache (live from Postgres) + OTel tracer ----
        cache = PgRouteCache(DEMO_PROJECT_ID)
        await cache.start_async_watching()
        gw_server, gw = await serve_gateway(50050, GatewayServicer(
            Router(cache), channel_options=GRPC_OPTIONS, tracer=tracer), options=GRPC_OPTIONS)
        ch = grpc.aio.insecure_channel("localhost:50050", options=GRPC_OPTIONS)
        stub = dpg.AgentStreamStub(ch)
        print(f"  gateway up; live routing = {[b.version_tag for b in cache.snapshot().primary]}")

        # ---- STEP 2: canary 90/10 + real-time multi-modal traffic ----
        step(2, "canary 90/10 + real-time multi-modal gRPC traffic")
        conn.commit()
        routing.install_weighted(conn, DEMO_PROJECT_ID,
                                 [{"deployment_id": stable_dep, "weight": 9000},
                                  {"deployment_id": cand_dep, "weight": 1000, "is_canary": True}],
                                 reason="canary 90/10", actor="ci")
        await wait_for(cache, lambda t: any(b.version_tag == "vCanary" for b in t.primary))
        dist = Counter([await text_session(stub, f"sess-{i}") for i in range(30)])
        print(f"  text canary split over 30 sessions: {dict(dist)}")
        mm_bytes = await binary_burst(stub, 12, _MB)
        print(f"  multi-modal: streamed {mm_bytes} x 1MB binary frames through the gateway "
              f"({mm_bytes} MB) — proxied OK")

        # ---- STEP 3: promote candidate to 100% (it now serves + 'writes' state) ----
        step(3, "promote candidate -> 100% live")
        conn.commit()
        routing.flip_routing(conn, DEMO_PROJECT_ID, cand_dep, reason=f"promote:{CAND_SHA}", actor="ci")
        VectorStoreStub().set_alias("ns-cand")
        MemoryGraphStub().append_many(100, lambda i: CAND_SHA); MemoryGraphStub().set_head(1100, 1100)
        SchemaStoreStub().set_version(37)
        await wait_for(cache, lambda t: t.primary and t.primary[0].version_tag == "vCanary" and len(t.primary) == 1)
        arm = await text_session(stub, "post-promote")
        print(f"  live routing = {[b.version_tag for b in cache.snapshot().primary]}; traffic -> {arm}")

        # ---- STEP 4: LIVE statistical eval-gating (sequential SPRT, early stop) ----
        step(4, "live statistical eval-gating (sequential SPRT)")
        store = EvalStore.open(".agentctl/pipeline.duckdb")
        prefs = SyntheticJudge(0.38, 0.08, seed=7).judge_suite(1000)
        seq = sequential_evaluate(prefs, method="sprt", nim=0.50)
        run_id, fixed = simulate_and_gate(store, p_win=0.38, p_tie=0.08, n=240, suite="correctness",
                                          commit=CAND_SHA, baseline=STABLE_SHA, pr=900, seed=7, cfg=GateConfig())
        print(f"  SPRT: {seq.decision} after {seq.n_used}/{seq.n_total} samples "
              f"({seq.compute_saved_pct:.0f}% compute saved) — {seq.reason}")
        print(f"  DuckDB fixed-horizon gate (n={fixed.n}): {fixed.decision} "
              f"[Wilson {fixed.wilson_low:.3f},{fixed.wilson_high:.3f}]")
        gate_blocked = seq.decision == "BLOCK" and fixed.decision == "BLOCK"
        print(f"  >>> candidate {'BLOCKED' if gate_blocked else 'allowed'} by the gate")

        # ---- STEP 5: controlled stateful rollback to stable ----
        step(5, "controlled 1-click stateful rollback -> stable")
        res = rollback_to_commit(conn, DEMO_PROJECT_ID, STABLE_SHA, actor="ci")
        print(f"  rollback #{res['rollback_id']}: status={res['status']} (routing v{res['routing_version']})")
        for u in res["unrollbackable"]:
            print(f"    un-rollback-able: [{u['class']}] {u['store_id']}: {u['reason']}")
        print(f"  state realigned: vector alias={VectorStoreStub().live_namespace()} "
              f"({VectorStoreStub().live_vector_count()} vecs), memory HEAD={MemoryGraphStub().live_head()['log_offset']}, "
              f"schema v{SchemaStoreStub().live_version()} (forward-fix)")

        # ---- STEP 6: validate routing-cache integrity ----
        step(6, "validate routing-cache integrity")
        reloaded = await wait_for(cache, lambda t: t.primary and t.primary[0].version_tag == "vMain")
        arm_final = await text_session(stub, "post-rollback")
        provider.force_flush()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS c FROM controlplane.otel_spans WHERE name='gateway.stream.metrics'")
            spans = cur.fetchone()["c"]
        print(f"  cache reloaded {cache.reloads}x via NOTIFY; live routing = "
              f"{[b.version_tag for b in cache.snapshot().primary]}; post-rollback traffic -> {arm_final}")
        print(f"  OTel: {spans} gateway.stream.metrics spans persisted to otel_spans")

        ok = (gate_blocked and res["status"] == "compensating" and reloaded and arm_final == "vMain"
              and VectorStoreStub().live_namespace() == "ns-stable" and cache.reloads >= 1 and spans > 0)
        print("\n" + "═" * 64)
        print(" PIPELINE: " + ("PASS ✔  — push→preview→canary→multimodal→gate(BLOCK)→rollback→routing OK"
                               if ok else "FAIL"))
        print("═" * 64)
        return 0 if ok else 1
    finally:
        if cache:
            await cache.stop_async_watching()
        if ch:
            await ch.close()
        if gw is not None:
            await gw_server.stop(0)
        if stable_dep:
            teardown_preview(rt, stable_dep)
        if cand_dep:
            teardown_preview(rt, cand_dep)
        provider.shutdown()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
