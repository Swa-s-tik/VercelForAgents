"""Echo agent backend implementing AgentStream (Vertical B demo target).

Generation runs CONCURRENTLY with reading, so a mid-stream INTERRUPT actually cuts the
current turn short (barge-in) rather than being processed only after generation finishes.
Per-request knobs via frame.attributes: tokens (default 1), delay seconds (default 0).
"""
from __future__ import annotations

import asyncio
import itertools

import grpc

from agentctl.gateway import frames as F
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


class EchoAgent(dpg.AgentStreamServicer):
    def __init__(self, version_tag: str):
        self.version = version_tag

    async def Converse(self, request_iterator, context):
        out: asyncio.Queue = asyncio.Queue()
        interrupt = asyncio.Event()
        seq = itertools.count()
        gens: list[asyncio.Task] = []

        async def generate(frame):
            tokens = int(frame.attributes.get("tokens", "1"))
            delay = float(frame.attributes.get("delay", "0"))
            for k in range(tokens):
                if interrupt.is_set():
                    await out.put(F.new_turn_end(frame, next(seq), pb.INTERRUPTED, self.version))
                    return
                if delay:
                    await asyncio.sleep(delay)
                await out.put(F.new_text(frame, next(seq),
                                         f"[{self.version}] echo:{frame.text.content} tok{k}",
                                         self.version, partial=(k < tokens - 1)))
            await out.put(F.new_turn_end(frame, next(seq), pb.STOP, self.version))

        async def reader():
            async for frame in request_iterator:
                if frame.HasField("control") and frame.control.kind == pb.INTERRUPT:
                    interrupt.set()
                elif frame.HasField("text"):
                    gens.append(asyncio.create_task(generate(frame)))
            if gens:
                await asyncio.gather(*gens, return_exceptions=True)
            await out.put(None)

        rt = asyncio.create_task(reader())
        while True:
            item = await out.get()
            if item is None:
                break
            yield item
        await rt

    async def Health(self, request, context):
        return dp.HealthReply(ready=True, inflight_streams=0, max_streams=128,
                              version_tag=self.version)


async def serve(version_tag: str, port: int, options=None) -> grpc.aio.Server:
    server = grpc.aio.server(options=options)
    dpg.add_AgentStreamServicer_to_server(EchoAgent(version_tag), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server


async def serve_forever(version_tag: str, port: int) -> None:
    server = await serve(version_tag, port)
    print(f"echo agent '{version_tag}' listening on :{port}")
    await server.wait_for_termination()
