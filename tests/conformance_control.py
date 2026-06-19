"""Control-plane + Health message conformance builders (extends Workstream 4 beyond the Frame).

Covers the ControlPlane service messages (RouteTable/Backend/ShadowPolicy, ResolveRouteRequest,
WatchRequest, TelemetryBatch/Event/Ack) and the Health messages - exercising nested messages,
repeated fields, a map<string,double>, and enums. Same honest contract as the Frame suite: protobuf
deterministic marshaling is per-runtime canonical, so we assert (1) a Python golden lock and (2)
cross-runtime decode interop (Go decodes the Python wire, Python decodes the Go wire), NOT
byte-identical full messages.

Regenerate: `python tests/conformance_control.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "conformance_control.json"

# the message types we vector, in order
CONTROL_SPECS = [
    "ResolveRouteRequest", "WatchRequest", "ShadowPolicy", "Backend", "RouteTable",
    "TelemetryEvent", "TelemetryBatch", "TelemetryAck", "HealthRequest", "HealthReply",
]


def build_control(pb, dp, cp, name: str):
    """Build a representative instance of a control-plane / health message. Field values are fixed
    and mirrored verbatim by the Go builder (conformance_control.go)."""
    if name == "ResolveRouteRequest":
        return cp.ResolveRouteRequest(deployment_id="dep-7")
    if name == "WatchRequest":
        return cp.WatchRequest(deployment_ids=["dep-7", "dep-8"])
    if name == "ShadowPolicy":
        return cp.ShadowPolicy(mock_side_effects=True, sample_percent=10, max_added_latency_ms=50)
    if name == "Backend":
        return _backend(cp, "b-1", "localhost:50051", 9000, "vA", True)
    if name == "RouteTable":
        return cp.RouteTable(
            deployment_id="dep-7", version=42,
            primary=[_backend(cp, "b-1", "localhost:50051", 9000, "vA", True),
                     _backend(cp, "b-2", "localhost:50052", 1000, "vB", False)],
            shadow=[_backend(cp, "s-1", "localhost:50053", 0, "shadow", True)],
            sticky=cp.STICKY_SESSION, ttl_seconds=300)
    if name == "TelemetryEvent":
        return _event(cp, "s1", "vA")
    if name == "TelemetryBatch":
        return cp.TelemetryBatch(events=[_event(cp, "s1", "vA"), _event(cp, "s2", "vB")])
    if name == "TelemetryAck":
        return cp.TelemetryAck(accepted=2)
    if name == "HealthRequest":
        return dp.HealthRequest(deployment_id="dep-7")
    if name == "HealthReply":
        return dp.HealthReply(ready=True, inflight_streams=3, max_streams=100,
                              supported_modalities=[pb.TEXT, pb.IMAGE], version_tag="vA")
    raise ValueError(f"unknown control message: {name!r}")


def _backend(cp, bid, endpoint, weight, tag, binary):
    return cp.Backend(backend_id=bid, endpoint=endpoint, weight=weight, version_tag=tag,
                      accepts_binary=binary,
                      shadow_policy=cp.ShadowPolicy(mock_side_effects=True, sample_percent=10,
                                                    max_added_latency_ms=50))


def _event(cp, sid, arm):
    e = cp.TelemetryEvent(session_id=sid, deployment_id="dep-7", canary_arm=arm,
                          event_type="stream", labels={"region": "us"},
                          ts_unix_nanos=1700000000000000001)
    e.measures["latency_ms"] = 33.5      # map<string,double> - the new wire shape vs Frame's map
    e.measures["frames"] = 21.0
    return e


def regenerate() -> Path:
    from agentctl.gen import load
    pb, dp, _dpg, cp, _cpg = load()
    out = {"_doc": "Control-plane/Health conformance vectors. golden_hex = Python deterministic "
                   "marshal. Go decodes these (and Python decodes Go's wire) into the same logical "
                   "message. Regenerate via `python tests/conformance_control.py`.",
           "messages": []}
    for name in CONTROL_SPECS:
        msg = build_control(pb, dp, cp, name)
        out["messages"].append({"name": name,
                                "golden_hex": msg.SerializeToString(deterministic=True).hex()})
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=2) + "\n")
    return FIXTURE


if __name__ == "__main__":
    p = regenerate()
    print(f"wrote {p} ({len(CONTROL_SPECS)} messages)")
