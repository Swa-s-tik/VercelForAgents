"""Multi-modal gRPC stress test (Vertical B hardening).

Proves the Python grpc.aio proxy holds a heavy persistent bidi stream of large binary frames
(simulating uncompressed ~1 MB YOLO/TensorRT video frames at 30 FPS) without throttling.

Two phases, both through the REAL gateway -> mock vision agent:
  A) real-time: paced at the target FPS with a bounded send queue. If the proxy can't keep
     up, the producer DROPS frames (real-time pipelines can't buffer unbounded) -> a true
     frame-drop-rate metric. Reports drop rate, throughput, and end-to-end latency.
  B) burst: frames sent as fast as the stream accepts them -> the throughput CEILING.

gRPC buffers are pre-tuned for big frames (see agentctl/gateway/tuning.py).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

os.environ.setdefault("GRPC_VERBOSITY", "NONE")

import grpc
import numpy as np

from agentctl.agents.vision_sink import serve as serve_vision
from agentctl.gateway.proxy import GatewayServicer, serve as serve_gateway
from agentctl.gateway.route_cache import RouteCache
from agentctl.gateway.router import Backend, RouteTable, Router
from agentctl.gateway.tuning import GRPC_OPTIONS
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()
_MB = 1024 * 1024


def _frame(seq: int, payload: bytes) -> "pb.Frame":
    return pb.Frame(session_id="yolo", stream_id=1, seq=seq, direction=pb.CLIENT_TO_AGENT,
                    binary=pb.BinaryChunk(modality=pb.VIDEO_FRAME, codec="raw_rgb",
                                          index=seq, data=payload,
                                          pts_nanos=time.perf_counter_ns()))


async def run_realtime(stub, fps: int, size: int, total: int, qsize: int) -> dict:
    payload = os.urandom(size)
    queue: asyncio.Queue = asyncio.Queue(maxsize=qsize)
    latencies: list[float] = []
    sent = dropped = acked = 0

    async def req_gen():
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    call = stub.Converse(req_gen())

    async def producer():
        nonlocal sent, dropped
        start = time.perf_counter_ns()
        interval_ns = int(1e9 / fps)
        for i in range(total):
            target = start + i * interval_ns
            now = time.perf_counter_ns()
            if now < target:
                await asyncio.sleep((target - now) / 1e9)
            try:
                queue.put_nowait(_frame(i, payload))   # drop-on-full = real-time semantics
                sent += 1
            except asyncio.QueueFull:
                dropped += 1
        await queue.put(None)

    async def consumer():
        nonlocal acked
        async for resp in call:
            if resp.HasField("binary"):
                latencies.append((time.perf_counter_ns() - resp.binary.pts_nanos) / 1e6)
                acked += 1

    t0 = time.perf_counter()
    await asyncio.gather(producer(), consumer())
    elapsed = time.perf_counter() - t0
    lat = np.array(latencies) if latencies else np.array([0.0])
    return {"sent": sent, "dropped": dropped, "acked": acked, "total": total,
            "elapsed": elapsed, "fps": acked / elapsed, "mbps": acked * size / _MB / elapsed,
            "p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95)),
            "p99": float(np.percentile(lat, 99)), "max": float(lat.max())}


async def run_burst(stub, size: int, count: int) -> dict:
    payload = os.urandom(size)
    acked = 0

    async def req_gen():
        for i in range(count):
            yield _frame(i, payload)

    call = stub.Converse(req_gen())
    t0 = time.perf_counter()
    async for resp in call:
        if resp.HasField("binary"):
            acked += 1
    elapsed = time.perf_counter() - t0
    return {"acked": acked, "elapsed": elapsed,
            "fps": acked / elapsed, "mbps": acked * size / _MB / elapsed}


async def main(argv=None):
    ap = argparse.ArgumentParser(description="multi-modal gRPC stress test")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--mb", type=float, default=1.0, help="frame size in MB")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--qsize", type=int, default=8, help="bounded send queue (real-time buffer)")
    ap.add_argument("--burst", type=int, default=120)
    args = ap.parse_args(argv)
    size = int(args.mb * _MB)
    total = int(args.fps * args.seconds)

    agent = await serve_vision(50051, options=GRPC_OPTIONS)
    cache = RouteCache({"default": RouteTable("default", 1, (Backend("vision", "localhost:50051", 100, "vision"),))})
    gw, _ = await serve_gateway(50050, GatewayServicer(Router(cache), channel_options=GRPC_OPTIONS), options=GRPC_OPTIONS)
    channel = grpc.aio.insecure_channel("localhost:50050", options=GRPC_OPTIONS)
    stub = dpg.AgentStreamStub(channel)
    try:
        print("=" * 64)
        print(" Multi-modal gRPC stress test (YOLO/TensorRT-style frames)")
        print("=" * 64)
        print(f" target: {args.fps} FPS x {args.mb:.1f} MB frames for {args.seconds:.1f}s "
              f"({total} frames, {total * args.mb:.0f} MB)")
        print(f" buffers: max_msg=64MB, http2_frame=4MB, send_queue={args.qsize} frames")

        rt = await run_realtime(stub, args.fps, size, total, args.qsize)
        drop_pct = 100.0 * rt["dropped"] / max(rt["total"], 1)
        print("\n --- Phase A: real-time (paced) ---")
        print(f"  frames offered:    {rt['total']}")
        print(f"  frames delivered:  {rt['acked']} (acked round-trip)")
        print(f"  frames dropped:    {rt['dropped']}  ({drop_pct:.1f}%)")
        print(f"  elapsed:           {rt['elapsed']:.2f} s")
        print(f"  throughput:        {rt['fps']:.1f} FPS | {rt['mbps']:.1f} MB/s")
        print(f"  latency (ms):      p50={rt['p50']:.1f}  p95={rt['p95']:.1f}  "
              f"p99={rt['p99']:.1f}  max={rt['max']:.1f}")

        bu = await run_burst(stub, size, args.burst)
        print("\n --- Phase B: burst (max throughput ceiling) ---")
        print(f"  {bu['acked']} frames in {bu['elapsed']:.2f}s  ->  "
              f"{bu['fps']:.0f} FPS | {bu['mbps']:.0f} MB/s ceiling")

        steady = drop_pct < 2.0 and rt["fps"] >= args.fps * 0.9
        print("\n" + ("-" * 64))
        if steady:
            print(f" VERDICT: STREAM HELD STEADY ✔  (drop {drop_pct:.1f}% < 2%, "
                  f"{rt['fps']:.1f} FPS >= {args.fps * 0.9:.1f} FPS target)")
        else:
            print(f" VERDICT: THROTTLED ✗  (drop {drop_pct:.1f}%, {rt['fps']:.1f} FPS) "
                  f"- increase --qsize / buffers")
        return 0 if steady else 1
    finally:
        await channel.close()
        await gw.stop(0)
        await agent.stop(0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
