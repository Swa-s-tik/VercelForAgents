"""Record/replay cassettes for tool calls (Phase 7).

A cassette captures a real tool response once (e.g. from a run on main) keyed by
(tool, schema_hash, args_fingerprint) so a preview replays it deterministically and
side-effect-free. The schema_hash invalidates stale cassettes when a tool's schema changes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CASSETTE_DIR = os.environ.get("AGENTCTL_CASSETTE_DIR", ".agentctl/cassettes")


def _norm(obj) -> str:
    return json.dumps(obj or {}, sort_keys=True, default=str)


def args_fingerprint(args: dict) -> str:
    return hashlib.sha1(_norm(args).encode()).hexdigest()[:12]


def schema_hash(schema: dict | None) -> str:
    return hashlib.sha1(_norm(schema).encode()).hexdigest()[:12]


class CassetteStore:
    def __init__(self, root: str = CASSETTE_DIR):
        self.root = Path(root)

    def _path(self, tool: str, sh: str, afp: str) -> Path:
        return self.root / tool / f"{sh}-{afp}.json"

    def record(self, tool: str, args: dict, result, schema: dict | None = None) -> Path:
        p = self._path(tool, schema_hash(schema), args_fingerprint(args))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": tool, "args": args, "result": result}, default=str))
        return p

    def replay(self, tool: str, args: dict, schema: dict | None = None):
        p = self._path(tool, schema_hash(schema), args_fingerprint(args))
        if p.exists():
            return json.loads(p.read_text())["result"]
        return None
