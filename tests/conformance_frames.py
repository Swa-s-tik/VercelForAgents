"""Shared frame specs + builder for the golden-wire proto conformance suite (Workstream 4).

A single language-neutral spec (``FRAME_SPECS``) defines a set of ``Frame`` messages covering the
frozen header, every oneof payload, enums, bytes, and a multi-key ``attributes`` map (the map
ordering stress case). ``build_frame`` turns a spec into a protobuf ``Frame``; the Python test and
the Go test (``gateway_core/internal/gateway/conformance_test.go``) both build from this same JSON
spec, marshal deterministically, and assert the bytes equal the committed ``golden_hex``. If the
Python and Go wire encodings ever diverge, one side stops matching the golden.

Run ``python -m tests.conformance_frames`` (or ``python tests/conformance_frames.py``) to
regenerate ``tests/fixtures/conformance_frames.json`` with fresh goldens after an intentional
proto change.
"""
from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "conformance_frames.json"

# enum name -> wire value (mirrors proto/envelope.proto; Go uses the same string keys)
ENUM = {
    "direction": {"DIRECTION_UNSPECIFIED": 0, "CLIENT_TO_AGENT": 1, "AGENT_TO_CLIENT": 2},
    "modality": {"MODALITY_UNSPECIFIED": 0, "TEXT": 1, "VIDEO_FRAME": 2, "AUDIO_PCM": 3,
                 "IMAGE": 4, "TENSOR": 5, "FILE": 6},
    "control_kind": {"CONTROL_UNSPECIFIED": 0, "INTERRUPT": 1, "CANCEL": 2, "FLOW_CREDIT": 3,
                     "PING": 4, "PONG": 5, "DRAIN": 6},
    "risk": {"RISK_UNSPECIFIED": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3},
    "decision": {"DECISION_UNSPECIFIED": 0, "APPROVE": 1, "DENY": 2, "ABSTAIN": 3},
    "finish_reason": {"FINISH_UNSPECIFIED": 0, "STOP": 1, "LENGTH": 2, "INTERRUPTED": 3,
                      "ERROR": 4, "TOOL": 5},
}

# The canonical test vectors. bytes fields are authored as hex strings.
FRAME_SPECS = [
    {"name": "client_text",
     "session_id": "sess-001", "stream_id": 7, "seq": 3, "direction": "CLIENT_TO_AGENT",
     "ts_unix_nanos": 1700000000000000001,
     "attributes": {"traceparent": "00-abc-def-01", "deployment_id": "42"},
     "payload": {"kind": "text", "content": "where is my refund?", "partial": False}},

    {"name": "agent_text_partial",
     "session_id": "sess-001", "stream_id": 7, "seq": 4, "direction": "AGENT_TO_CLIENT",
     "attributes": {"served_by": "v2"},
     "payload": {"kind": "text", "content": "Looking into order ", "partial": True}},

    {"name": "tool_call_side_effecting",
     "session_id": "sess-002", "stream_id": 1, "seq": 11, "direction": "AGENT_TO_CLIENT",
     "payload": {"kind": "tool_call", "call_id": "c-9", "tool_name": "issue_refund",
                 "arguments_hex": "7b226f72646572223a22412d32323931227d", "side_effecting": True}},

    {"name": "tool_result_mocked",
     "session_id": "sess-002", "stream_id": 1, "seq": 12, "direction": "AGENT_TO_CLIENT",
     "payload": {"kind": "tool_result", "call_id": "c-9", "result_hex": "6f6b",
                 "is_error": False, "mocked": True}},

    {"name": "control_interrupt",
     "session_id": "sess-003", "stream_id": 2, "seq": 0, "direction": "CLIENT_TO_AGENT",
     "payload": {"kind": "control", "control_kind": "INTERRUPT", "reason": "user barge-in",
                 "ack_seq": 5, "credits": 0}},

    {"name": "turn_end_stop",
     "session_id": "sess-001", "stream_id": 7, "seq": 99, "direction": "AGENT_TO_CLIENT",
     "payload": {"kind": "turn_end", "turn_id": "sess-001:7", "reason": "STOP",
                 "prompt_tokens": 128, "completion_tokens": 64}},

    {"name": "binary_image_chunk",
     "session_id": "sess-004", "stream_id": 3, "seq": 1, "direction": "CLIENT_TO_AGENT",
     "payload": {"kind": "binary", "modality": "IMAGE", "codec": "jpeg", "group_id": 88,
                 "index": 0, "last": True, "data_hex": "ffd8ffe000104a464946", "width": 640,
                 "height": 480, "pts_nanos": 123456789}},

    {"name": "approval_req_high",
     "session_id": "sess-005", "stream_id": 4, "seq": 2, "direction": "AGENT_TO_CLIENT",
     "payload": {"kind": "approval_req", "approval_id": "ap-1", "action_summary": "wire $5,000",
                 "action_payload_hex": "cafe", "expires_at_unix": 1700000123, "risk": "HIGH"}},

    # map-ordering stress: keys authored out of sorted order; deterministic marshal must sort them
    # identically on both runtimes.
    {"name": "attributes_multikey",
     "session_id": "sess-006", "stream_id": 9, "seq": 7, "direction": "AGENT_TO_CLIENT",
     "attributes": {"zeta": "1", "alpha": "2", "mid": "3", "canary_arm": "vB", "shadow": "true"},
     "payload": {"kind": "text", "content": "fan-out", "partial": False}},
]


def _bytes(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


def build_frame(pb, spec: dict):
    """Build a protobuf Frame from a spec dict (pb = envelope_pb2 module)."""
    f = pb.Frame(session_id=spec["session_id"], stream_id=spec["stream_id"], seq=spec["seq"],
                 direction=ENUM["direction"][spec["direction"]])
    if spec.get("ts_unix_nanos"):
        f.ts_unix_nanos = spec["ts_unix_nanos"]
    p = spec["payload"]
    k = p["kind"]
    if k == "text":
        f.text.CopyFrom(pb.TextDelta(content=p["content"], partial=p.get("partial", False)))
    elif k == "tool_call":
        f.tool_call.CopyFrom(pb.ToolCall(call_id=p["call_id"], tool_name=p["tool_name"],
                                         arguments=_bytes(p["arguments_hex"]),
                                         side_effecting=p["side_effecting"]))
    elif k == "tool_result":
        f.tool_result.CopyFrom(pb.ToolResult(call_id=p["call_id"], result=_bytes(p["result_hex"]),
                                             is_error=p["is_error"], mocked=p["mocked"]))
    elif k == "control":
        f.control.CopyFrom(pb.Control(kind=ENUM["control_kind"][p["control_kind"]],
                                      reason=p.get("reason", ""), ack_seq=p.get("ack_seq", 0),
                                      credits=p.get("credits", 0)))
    elif k == "turn_end":
        f.turn_end.CopyFrom(pb.TurnEnd(turn_id=p["turn_id"], reason=ENUM["finish_reason"][p["reason"]],
                                       prompt_tokens=p.get("prompt_tokens", 0),
                                       completion_tokens=p.get("completion_tokens", 0)))
    elif k == "binary":
        f.binary.CopyFrom(pb.BinaryChunk(modality=ENUM["modality"][p["modality"]], codec=p["codec"],
                                         group_id=p["group_id"], index=p["index"], last=p["last"],
                                         data=_bytes(p["data_hex"]), width=p["width"],
                                         height=p["height"], pts_nanos=p["pts_nanos"]))
    elif k == "approval_req":
        f.approval_req.CopyFrom(pb.ApprovalRequest(
            approval_id=p["approval_id"], action_summary=p["action_summary"],
            action_payload=_bytes(p["action_payload_hex"]), expires_at_unix=p["expires_at_unix"],
            risk=ENUM["risk"][p["risk"]]))
    else:
        raise ValueError(f"unknown payload kind: {k!r}")
    for key, val in spec.get("attributes", {}).items():
        f.attributes[key] = val
    return f


def golden_hex(pb, spec: dict) -> str:
    return build_frame(pb, spec).SerializeToString(deterministic=True).hex()


def header_hex(pb, spec: dict) -> str:
    """Marshal a header-only Frame (frozen fields 1-4). These bytes ARE byte-identical across
    runtimes - no oneof/map to reorder - which is exactly the contract the gateway's header-only
    forwarding relies on."""
    h = pb.Frame(session_id=spec["session_id"], stream_id=spec["stream_id"], seq=spec["seq"],
                 direction=ENUM["direction"][spec["direction"]])
    return h.SerializeToString(deterministic=True).hex()


def regenerate() -> Path:
    from agentctl.gen import load
    pb = load()[0]
    out = {"_doc": "Golden-wire conformance vectors. golden_hex = Python deterministic proto "
                   "marshal (the reference wire). header_hex = marshal of the frozen header "
                   "(fields 1-4), which IS byte-identical across runtimes. NOTE: protobuf "
                   "deterministic marshaling is per-runtime canonical, not cross-runtime - Go "
                   "orders the oneof after the map, Python before it - so the conformance contract "
                   "is (a) byte-identical frozen header + (b) lossless cross-runtime decode, NOT "
                   "byte-identical full frames. Regenerate via `python tests/conformance_frames.py`.",
           "frames": []}
    for spec in FRAME_SPECS:
        out["frames"].append({**spec, "golden_hex": golden_hex(pb, spec),
                              "header_hex": header_hex(pb, spec)})
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=2) + "\n")
    return FIXTURE


if __name__ == "__main__":
    p = regenerate()
    print(f"wrote {p} ({len(FRAME_SPECS)} frames)")
