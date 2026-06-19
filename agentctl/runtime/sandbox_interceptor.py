"""Tool-call sandbox interceptor (Phase 7).

Agents reach external tools through ONE dispatch seam (`ToolInvoker.invoke`). In preview /
shadow mode we inject a `SandboxInterceptor` instead of the live invoker: side-effecting,
write, or external tool calls are caught and answered side-effect-free -
  * write tools mutate an in-memory MockStateEnvironment (never the real backend);
  * external tools are resolved via the mock registry (cassette -> stub -> autogen);
read/pure tools may pass through. Every mocked call is flagged so traces show `mocked=true`,
matching the Frame.ToolCall.side_effecting bit on the gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from agentctl.mocking.registry import MockRegistry

ToolClass = Literal["read", "write", "external"]


@dataclass
class Tool:
    name: str
    side_effecting: bool = False
    klass: ToolClass = "read"
    schema: dict | None = None
    output_schema: dict | None = None
    fn: Callable[[dict], object] | None = None   # the REAL execution


@dataclass
class ToolResult:
    result: object
    mocked: bool = False
    error: bool = False
    source: str = "real"      # real | sandbox | cassette | stub | autogen


class MockStateEnvironment:
    """A sandboxed key-value store. Preview write tools mutate THIS, never the real backend."""

    def __init__(self):
        self.store: dict = {}

    def write(self, key, value):
        self.store[key] = value

    def read(self, key):
        return self.store.get(key)


class ToolInvoker:
    """The real (production) tool dispatcher."""

    def __init__(self, tools: list[Tool]):
        self.tools: dict[str, Tool] = {t.name: t for t in tools}

    def invoke(self, name: str, args: dict) -> ToolResult:
        tool = self.tools[name]
        try:
            out = tool.fn(args) if tool.fn else None
            return ToolResult(result=out, mocked=False, source="real")
        except Exception as e:  # surface tool errors structurally
            return ToolResult(result=str(e), mocked=False, error=True, source="real")


class SandboxInterceptor:
    """Wraps a ToolInvoker. In preview mode, intercepts unsafe calls and answers them safely."""

    def __init__(self, invoker: ToolInvoker, registry: MockRegistry | None = None,
                 sandbox: MockStateEnvironment | None = None, mode: str = "preview"):
        self.invoker = invoker
        self.registry = registry or MockRegistry()
        self.sandbox = sandbox or MockStateEnvironment()
        self.mode = mode
        self.intercepted = 0
        self.passed = 0

    def _must_mock(self, tool: Tool) -> bool:
        return self.mode == "preview" and (tool.side_effecting or tool.klass in ("write", "external"))

    def invoke(self, name: str, args: dict) -> ToolResult:
        tool = self.invoker.tools[name]
        if not self._must_mock(tool):
            self.passed += 1
            return self.invoker.invoke(name, args)   # read/pure -> real (safe)

        self.intercepted += 1
        if tool.klass == "write":
            self.sandbox.write(args.get("key"), args.get("value"))
            return ToolResult(result={"written": args.get("key"), "sandbox": True},
                              mocked=True, source="sandbox")
        res, source = self.registry.resolve(
            name, args, schema=tool.schema, output_schema=tool.output_schema)
        return ToolResult(result=res, mocked=True, source=source)
