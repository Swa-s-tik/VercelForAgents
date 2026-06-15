"""StateStore interface + the JSON-backed persistence used by the demo stubs.

The three stubs (vector / memory / schema) honor this interface. Real adapters
(Pinecone/Qdrant/pgvector, an event-sourced memory graph, a migration runner) drop in
without touching the rollback orchestrator. State is persisted to a small JSON file so the
`seed` and `rollback` CLI steps can run as separate processes and share simulated state.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from agentctl.config import STATE_FILE


def digest(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()[:16]


@runtime_checkable
class StateStore(Protocol):
    store_id: str
    def snapshot(self, coordinate: dict) -> str: ...     # capture-time digest of a coordinate
    def restore(self, coordinate: dict) -> str: ...      # drive live state to coordinate; return live digest
    def live_digest(self) -> str: ...                    # digest of the CURRENT live state


class JsonBackend:
    """Tiny read-modify-write JSON store (single-process demo; not concurrency-safe)."""

    def __init__(self, path: str = STATE_FILE):
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    def set(self, key: str, value) -> None:
        data = self.load()
        data[key] = value
        self.save(data)
