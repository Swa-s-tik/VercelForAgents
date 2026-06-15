"""WebSocket -> gRPC edge bridge (Vertical B).

One browser WebSocket <-> one internal gRPC Converse stream. Translates JSON frames to/from
the protobuf envelope. Maps a client INTERRUPT message to a Control{INTERRUPT} frame
(barge-in: stream stays open) and maps WS-close to gRPC stream cancellation.
"""
from __future__ import annotations

import asyncio
import itertools
import json

import grpc
import websockets

from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


class WsBridge:
    def __init__(self, gateway_endpoint: str = "localhost:50050"):
        self.endpoint = gateway_endpoint

    async def handler(self, ws):
        channel = grpc.aio.insecure_channel(self.endpoint)
        stub = dpg.AgentStreamStub(channel)
        session_id = f"ws-{id(ws):x}"
        seq = itertools.count()
        outbound: asyncio.Queue = asyncio.Queue()

        async def to_grpc_gen():
            while True:
                item = await outbound.get()
                if item is None:
                    return
                yield item

        call = stub.Converse(to_grpc_gen())

        async def ws_to_grpc():
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    sid = int(msg.get("stream_id", 1))
                    if msg.get("type") == "interrupt":
                        await outbound.put(pb.Frame(session_id=session_id, stream_id=sid, seq=next(seq),
                                                    direction=pb.CLIENT_TO_AGENT,
                                                    control=pb.Control(kind=pb.INTERRUPT)))
                    else:  # text
                        f = pb.Frame(session_id=session_id, stream_id=sid, seq=next(seq),
                                     direction=pb.CLIENT_TO_AGENT,
                                     text=pb.TextDelta(content=msg.get("content", "")))
                        for k in ("tokens", "delay"):
                            if k in msg:
                                f.attributes[k] = str(msg[k])
                        await outbound.put(f)
            finally:
                await outbound.put(None)   # signal done-writing to the gRPC stream

        async def grpc_to_ws():
            try:
                async for resp in call:
                    out = {"session_id": resp.session_id, "stream_id": resp.stream_id,
                           "served_by": resp.attributes.get("served_by", ""),
                           "canary_arm": resp.attributes.get("canary_arm", "")}
                    if resp.HasField("text"):
                        out.update(type="text", content=resp.text.content)
                    elif resp.HasField("turn_end"):
                        out.update(type="turn_end", reason=int(resp.turn_end.reason))
                    else:
                        out.update(type="other")
                    await ws.send(json.dumps(out))
            except Exception:
                pass

        t1 = asyncio.create_task(ws_to_grpc())
        t2 = asyncio.create_task(grpc_to_ws())
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            call.cancel()                  # WS close / stream end -> gRPC cancellation
            for t in (t1, t2):
                t.cancel()
            await channel.close()


async def serve(ws_port: int = 8765, gateway_endpoint: str = "localhost:50050"):
    bridge = WsBridge(gateway_endpoint)
    return await websockets.serve(bridge.handler, "localhost", ws_port)


async def serve_forever(ws_port: int = 8765, gateway_endpoint: str = "localhost:50050") -> None:
    await serve(ws_port, gateway_endpoint)
    print(f"ws-bridge listening on ws://localhost:{ws_port} -> {gateway_endpoint}")
    await asyncio.Future()   # run forever
