"""Mock customer-support agent (Milestone 2) - the community-relatable example backend.

Streams a reply token-by-token as chunked TextDelta frames (simulating LLM token streaming),
then emits an `issue_refund` ToolCall frame (side-effecting). Exercises the Frame envelope
properly: TextDelta for the stream, ToolCall for the tool. The per-token delay lets a client
prove the gateway streams chunks incrementally instead of buffering one giant block.
"""
from __future__ import annotations

import asyncio

import grpc

from agentctl.gateway import frames as F
from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()

_REPLY = ("I'm sorry about the trouble with your order. I've checked the details and "
          "I'll process a refund for you right away.")


class SupportAgent(dpg.AgentStreamServicer):
    def __init__(self, version_tag: str = "support", token_delay: float = 0.03):
        self.version = version_tag
        self.token_delay = token_delay

    async def Converse(self, request_iterator, context):
        async for frame in request_iterator:
            if not frame.HasField("text"):
                continue
            seq = 0
            tokens = _REPLY.split(" ")
            for tok in tokens:                       # stream token-by-token (TextDelta frames)
                await asyncio.sleep(self.token_delay)
                yield F.new_text(frame, seq, tok + " ", self.version, partial=True)
                seq += 1
            yield self._refund_tool_call(frame, seq)  # side-effecting ToolCall frame
            seq += 1
            yield F.new_turn_end(frame, seq, pb.STOP, self.version)

    def _refund_tool_call(self, src, seq: int):
        f = pb.Frame(session_id=src.session_id, stream_id=src.stream_id, seq=seq,
                     direction=pb.AGENT_TO_CLIENT,
                     tool_call=pb.ToolCall(call_id=f"call-{seq}", tool_name="issue_refund",
                                           arguments=b'{"order_id":"A-2291","amount":29.99}',
                                           side_effecting=True))
        f.attributes["served_by"] = self.version
        return f

    async def Health(self, request, context):
        return dp.HealthReply(ready=True, max_streams=128, version_tag=self.version)


async def serve(version_tag: str, port: int, options=None) -> grpc.aio.Server:
    server = grpc.aio.server(options=options)
    dpg.add_AgentStreamServicer_to_server(SupportAgent(version_tag), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server


async def serve_forever(version_tag: str, port: int) -> None:
    server = await serve(version_tag, port)
    print(f"support agent '{version_tag}' listening on :{port}")
    await server.wait_for_termination()
