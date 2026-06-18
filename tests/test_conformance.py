"""Golden-wire proto conformance — Python side (Workstream 4).

The contract (proven jointly with gateway_core/internal/gateway/conformance_test.go):

  1. The Python runtime marshals each canonical Frame to the committed ``golden_hex`` (regression
     lock on the reference wire).
  2. The frozen header (fields 1-4) marshals to ``header_hex`` — bytes that are byte-identical
     across Python and Go (the contract the gateway's header-only forwarding relies on).
  3. Python losslessly decodes Go's wire (``conformance_go_wire.json``, emitted by ``make
     fixtures``) into the same logical frame — the cross-runtime interop guarantee.

Note: protobuf ``deterministic=True`` is per-runtime canonical, NOT cross-runtime (Go orders the
oneof after the map, Python before it), so full frames are intentionally NOT byte-identical — only
the frozen header is, and decode interop holds both directions. No DB or network.
"""
from __future__ import annotations

import json

import pytest

from agentctl.gen import load
from tests.conformance_frames import FIXTURE, build_frame, header_hex

pb = load()[0]
FRAMES = json.loads(FIXTURE.read_text())["frames"]
IDS = [f["name"] for f in FRAMES]
GO_WIRE = FIXTURE.parent / "conformance_go_wire.json"


@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_python_marshal_locks_golden(spec):
    got = build_frame(pb, spec).SerializeToString(deterministic=True).hex()
    assert got == spec["golden_hex"], f"{spec['name']}: Python wire drift vs golden"


@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_frozen_header_byte_identical(spec):
    # the cross-runtime byte-identical contract; the Go test asserts the same header_hex value.
    assert header_hex(pb, spec) == spec["header_hex"]


@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_roundtrip_stable(spec):
    raw = bytes.fromhex(spec["golden_hex"])
    f = pb.Frame()
    f.ParseFromString(raw)
    assert f.session_id == spec["session_id"]
    assert f.seq == spec["seq"]
    assert f.SerializeToString(deterministic=True) == raw


@pytest.mark.skipif(not GO_WIRE.exists(),
                    reason="run `cd agentctl/gateway_core && make fixtures` to emit Go wire")
@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_python_decodes_go_wire(spec):
    """Python parses Go's deterministic wire into the same logical frame it would build."""
    go_hex = json.loads(GO_WIRE.read_text())["frames"][spec["name"]]
    f = pb.Frame()
    f.ParseFromString(bytes.fromhex(go_hex))
    expected = build_frame(pb, spec).SerializeToString(deterministic=True)
    assert f.SerializeToString(deterministic=True) == expected, \
        f"{spec['name']}: Python decode of Go wire != expected frame"


def test_all_payload_kinds_and_header_covered():
    kinds = {f["payload"]["kind"] for f in FRAMES}
    assert kinds == {"text", "tool_call", "tool_result", "control", "turn_end", "binary",
                     "approval_req"}
    assert {f["direction"] for f in FRAMES} >= {"CLIENT_TO_AGENT", "AGENT_TO_CLIENT"}
