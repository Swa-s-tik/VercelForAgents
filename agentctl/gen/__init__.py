"""Generated gRPC stubs live here (regenerated from ../../proto via grpc_tools.protoc).

The generated *_pb2 modules import each other by top-level name (``import envelope_pb2``),
so we put this directory on sys.path and load them as top-level modules exactly once
(avoids protobuf's duplicate-descriptor-pool error from importing under two names).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def load():
    """Import and return the generated modules:
    (envelope_pb2, dataplane_pb2, dataplane_pb2_grpc, controlplane_pb2, controlplane_pb2_grpc)."""
    import controlplane_pb2
    import controlplane_pb2_grpc
    import dataplane_pb2
    import dataplane_pb2_grpc
    import envelope_pb2
    return (envelope_pb2, dataplane_pb2, dataplane_pb2_grpc,
            controlplane_pb2, controlplane_pb2_grpc)
