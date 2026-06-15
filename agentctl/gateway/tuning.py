"""gRPC channel/server tuning for heavy multi-modal (vision/audio) bidi streams.

Defaults are sized for ~1 MB uncompressed video frames at 30 FPS through the proxy:
large max-message limits, a bigger HTTP/2 frame size (fewer round-trips per 1 MB payload),
and BDP-probe-driven dynamic flow-control windows so the persistent stream doesn't stall.
"""
from __future__ import annotations

_MB = 1024 * 1024

# Applied to both the gateway<->backend channels and every server.
GRPC_OPTIONS = [
    ("grpc.max_send_message_length", 64 * _MB),
    ("grpc.max_receive_message_length", 64 * _MB),
    ("grpc.http2.max_frame_size", 4 * _MB),          # large HTTP/2 frames for 1 MB payloads
    ("grpc.http2.bdp_probe", 1),                     # dynamic flow-control window sizing
    ("grpc.http2.write_buffer_size", 8 * _MB),
    ("grpc.so_reuseport", 0),
]
