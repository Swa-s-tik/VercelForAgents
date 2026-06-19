"""Parity tests for the Python proxy's header-only primitives (agentctl/gateway/wire.py): they agree
with the typed path on every golden conformance fixture - the same fixtures the Go wire fast path is
pinned against - so the two runtimes stay header-compatible."""
from __future__ import annotations

import json

import pytest

from agentctl.gateway import wire
from agentctl.gen import load
from tests.conformance_frames import FIXTURE, build_frame

pb, _dp, _dpg, _cp, _cpg = load()
FRAMES = json.loads(FIXTURE.read_text())["frames"]
IDS = [f["name"] for f in FRAMES]


@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_session_id_matches_typed(spec):
    f = build_frame(pb, spec)
    raw = f.SerializeToString()
    got = wire.session_id(raw)
    if f.session_id:
        assert got == f.session_id
    else:
        assert got in (None, "")


@pytest.mark.parametrize("spec", FRAMES, ids=IDS)
def test_set_canary_arm_equivalent_to_typed(spec):
    f = build_frame(pb, spec)
    raw = f.SerializeToString()

    got = pb.Frame()
    got.ParseFromString(wire.set_canary_arm(raw, "v37-canary"))

    want = pb.Frame()
    want.CopyFrom(f)
    want.attributes["canary_arm"] = "v37-canary"
    assert got == want


def test_session_id_absent_and_malformed():
    assert wire.session_id(b"") is None
    assert wire.session_id(b"\xff\xff") is None
    f = pb.Frame(seq=1)                      # field 1 unset
    assert wire.session_id(f.SerializeToString()) in (None, "")


def test_set_canary_arm_overrides_existing():
    f = pb.Frame(session_id="s", seq=9)
    f.attributes["canary_arm"] = "stale"
    f.attributes["k"] = "v"
    got = pb.Frame()
    got.ParseFromString(wire.set_canary_arm(f.SerializeToString(), "fresh"))
    assert got.attributes["canary_arm"] == "fresh"          # append wins
    assert got.attributes["k"] == "v" and got.session_id == "s" and got.seq == 9
    assert wire.session_id(wire.set_canary_arm(f.SerializeToString(), "fresh")) == "s"
