"""Mock vision agent backend (Vertical B stress target).

Drains inbound BinaryChunk frames (simulated video/TensorRT input) as fast as it can and
emits a TINY ack per frame echoing (index, pts_nanos) so the client can measure end-to-end
latency without sending the 1 MB payload back. This isolates the proxy's UPLOAD path -
the heavy direction for a vision pipeline.
"""
from __future__ import annotations

import grpc

from agentctl.gen import load

pb, dp, dpg, _cp, _cpg = load()


class VisionSinkAgent(dpg.AgentStreamServicer):
    async def Converse(self, request_iterator, context):
        async for frame in request_iterator:
            if frame.HasField("binary"):
                bc = frame.binary
                # ack carries back the original send timestamp + index; payload-free.
                yield pb.Frame(
                    session_id=frame.session_id, stream_id=frame.stream_id, seq=bc.index,
                    direction=pb.AGENT_TO_CLIENT,
                    binary=pb.BinaryChunk(modality=bc.modality, index=bc.index,
                                          pts_nanos=bc.pts_nanos, last=bc.last))
            elif frame.HasField("control") and frame.control.kind == pb.DRAIN:
                yield pb.Frame(session_id=frame.session_id, stream_id=frame.stream_id, seq=0,
                               direction=pb.AGENT_TO_CLIENT,
                               turn_end=pb.TurnEnd(turn_id=frame.session_id, reason=pb.STOP))

    async def Health(self, request, context):
        return dp.HealthReply(ready=True, max_streams=256, version_tag="vision",
                              supported_modalities=[pb.VIDEO_FRAME, pb.IMAGE, pb.TENSOR])


async def serve(port: int, options=None) -> grpc.aio.Server:
    server = grpc.aio.server(options=options)
    dpg.add_AgentStreamServicer_to_server(VisionSinkAgent(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server
