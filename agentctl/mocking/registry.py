"""Schema-keyed mock registry (Phase 7).

Resolves a tool call to a side-effect-free response, in order:
  1. cassette hit (record/replay)
  2. a developer-registered synthetic stub  (@registry.mock_tool("send_email"))
  3. schema-driven autogen fallback (a minimal response satisfying the output schema)
So a preview agent can NEVER make a real external call — there is always a safe answer.
"""
from __future__ import annotations

from typing import Callable

from agentctl.mocking.cassette import CassetteStore

_AUTOGEN_DEFAULTS = {
    "string": "mock", "number": 0.0, "integer": 0, "boolean": False,
    "array": [], "object": {}, "null": None,
}


class MockRegistry:
    def __init__(self, cassettes: CassetteStore | None = None):
        self.cassettes = cassettes or CassetteStore()
        self.stubs: dict[str, Callable[[dict], object]] = {}

    def register(self, tool: str, fn: Callable[[dict], object]) -> Callable:
        self.stubs[tool] = fn
        return fn

    def mock_tool(self, tool: str):
        def deco(fn):
            return self.register(tool, fn)
        return deco

    def autogen(self, output_schema: dict | None) -> object:
        props = (output_schema or {}).get("properties")
        if props:
            return {k: _AUTOGEN_DEFAULTS.get(v.get("type", "object")) for k, v in props.items()}
        return _AUTOGEN_DEFAULTS.get((output_schema or {}).get("type", "object"), {"mocked": True})

    def resolve(self, tool: str, args: dict, *, schema: dict | None = None,
                output_schema: dict | None = None) -> tuple[object, str]:
        replayed = self.cassettes.replay(tool, args, schema)
        if replayed is not None:
            return replayed, "cassette"
        if tool in self.stubs:
            return self.stubs[tool](args), "stub"
        return self.autogen(output_schema), "autogen"
