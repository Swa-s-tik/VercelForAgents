"""The Python proxy's header-only raw path (raw_converse): drives it with a fake router + fake raw
backend call, proving it routes by scanning session_id, forwards inbound bytes to the primary, and
tags canary_arm on the outbound bytes - all without building a Frame or a real gRPC channel."""
from __future__ import annotations

import asyncio
import types

import pytest

from agentctl.gateway import wire
from agentctl.gateway.proxy import GatewayServicer
from agentctl.gen import load

pb, _dp, _dpg, _cp, _cpg = load()


def _frame_bytes(session_id, seq, content):
    f = pb.Frame(session_id=session_id, seq=seq)
    f.text.content = content
    return f.SerializeToString()


class FakeCall:
    """Stand-in for a raw bidi Converse call: records writes, yields canned responses on iteration."""
    def __init__(self, responses):
        self._responses, self.written, self.done = responses, [], False

    async def write(self, b): self.written.append(b)
    async def done_writing(self): self.done = True

    async def __aiter__(self):
        for r in self._responses:
            yield r


async def _drive(servicer, inputs):
    async def aiter():
        for b in inputs:
            yield b
    ctx = types.SimpleNamespace()
    return [out async for out in servicer.raw_converse(aiter(), ctx)]


def test_raw_converse_routes_forwards_and_tags():
    servicer = GatewayServicer.__new__(GatewayServicer)   # bypass __init__ (no PG/route cache)
    servicer._tracer = None
    servicer.stats = {"sessions": 0, "by_arm": {}, "shadow_sent": 0, "shadow_dropped": 0,
                      "shadow_received": 0}

    # fake router: a fixed arm + primary endpoint, no shadows
    decision = types.SimpleNamespace(
        arm="vB", primary=types.SimpleNamespace(endpoint="backend:1"), shadows=[])
    seen = {}

    def _resolve(sid):
        seen["sid"] = sid
        return decision
    servicer.router = types.SimpleNamespace(resolve=_resolve)

    # the primary "backend" echoes two response frames
    responses = [_frame_bytes("sess-xyz", 1, "hello "), _frame_bytes("sess-xyz", 2, "world")]
    primary = FakeCall(responses)
    servicer._raw_stub = lambda endpoint: (lambda: primary)

    inputs = [_frame_bytes("sess-xyz", 1, "in-1"), _frame_bytes("sess-xyz", 2, "in-2")]
    outs = asyncio.run(_drive(servicer, inputs))

    # routed by scanning session_id from the first frame
    assert seen["sid"] == "sess-xyz" and servicer.stats["by_arm"] == {"vB": 1}
    # inbound frames were forwarded verbatim to the primary
    assert primary.written == inputs and primary.done is True
    # every outbound frame carries canary_arm=vB and keeps its original content
    assert len(outs) == 2
    for raw, original in zip(outs, responses):
        f = pb.Frame()
        f.ParseFromString(raw)
        assert f.attributes["canary_arm"] == "vB"
        assert wire.session_id(raw) == "sess-xyz"          # frozen header intact
    f0 = pb.Frame(); f0.ParseFromString(outs[0])
    assert f0.text.content == "hello "


def test_raw_converse_empty_stream_is_noop():
    servicer = GatewayServicer.__new__(GatewayServicer)
    servicer._tracer = None
    servicer.stats = {"sessions": 0, "by_arm": {}}
    servicer.router = types.SimpleNamespace(resolve=lambda sid: pytest.fail("should not route"))
    assert asyncio.run(_drive(servicer, [])) == []
