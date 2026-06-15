"""Tool sandbox interceptor tests (Phase 7). Runs under pytest or as a plain script."""
from __future__ import annotations

import tempfile

from agentctl.mocking.cassette import CassetteStore
from agentctl.mocking.registry import MockRegistry
from agentctl.runtime.sandbox_interceptor import (
    SandboxInterceptor,
    Tool,
    ToolInvoker,
)


def _fresh_registry() -> MockRegistry:
    """Registry with an isolated, empty cassette dir so tests don't pollute each other."""
    return MockRegistry(cassettes=CassetteStore(tempfile.mkdtemp()))

# Real-world state that MUST remain untouched during a preview.
REAL_DB: dict = {}
EMAILS_SENT: list = []


def _build():
    REAL_DB.clear()
    EMAILS_SENT.clear()
    tools = [
        Tool("db_write", side_effecting=True, klass="write",
             fn=lambda a: REAL_DB.__setitem__(a["key"], a["value"])),
        Tool("send_email", side_effecting=True, klass="external",
             output_schema={"type": "object",
                            "properties": {"status": {"type": "string"}, "id": {"type": "integer"}}},
             fn=lambda a: EMAILS_SENT.append(a)),
        Tool("lookup", side_effecting=False, klass="read",
             fn=lambda a: REAL_DB.get(a["key"], "default")),
    ]
    return ToolInvoker(tools)


def test_write_hits_sandbox_not_real_db():
    sbx = SandboxInterceptor(_build(), mode="preview")
    r = sbx.invoke("db_write", {"key": "balance", "value": 999})
    assert r.mocked and r.source == "sandbox"
    assert REAL_DB == {}, "real DB was mutated during preview!"
    assert sbx.sandbox.read("balance") == 999


def test_external_call_is_mocked_no_side_effect():
    sbx = SandboxInterceptor(_build(), registry=_fresh_registry(), mode="preview")
    r = sbx.invoke("send_email", {"to": "ceo@corp.com", "body": "oops"})
    assert r.mocked and r.source == "autogen"
    assert r.result == {"status": "mock", "id": 0}    # schema-valid autogen
    assert EMAILS_SENT == [], "a real email was sent during preview!"


def test_registered_stub_takes_precedence():
    reg = _fresh_registry()
    reg.register("send_email", lambda a: {"status": "queued", "id": 42})
    sbx = SandboxInterceptor(_build(), registry=reg, mode="preview")
    r = sbx.invoke("send_email", {"to": "x"})
    assert r.source == "stub" and r.result["id"] == 42 and EMAILS_SENT == []


def test_cassette_replay():
    reg = _fresh_registry()
    reg.cassettes.record("send_email", {"to": "x"}, {"status": "sent", "id": 7})
    sbx = SandboxInterceptor(_build(), registry=reg, mode="preview")
    r = sbx.invoke("send_email", {"to": "x"})
    assert r.source == "cassette" and r.result["id"] == 7


def test_read_passes_through_to_real():
    inv = _build()
    REAL_DB["k"] = "real-value"
    sbx = SandboxInterceptor(inv, mode="preview")
    r = sbx.invoke("lookup", {"key": "k"})
    assert not r.mocked and r.source == "real" and r.result == "real-value"
    assert sbx.passed == 1 and sbx.intercepted == 0


def test_production_mode_runs_real():
    sbx = SandboxInterceptor(_build(), mode="production")
    sbx.invoke("db_write", {"key": "x", "value": 1})
    assert REAL_DB == {"x": 1}    # production actually executes


if __name__ == "__main__":
    import os
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("sandbox interceptor tests passed")
