"""Conversational-memory-graph stub: event-sourced rewind of the HEAD pointer (reversible).

Real strategy: the graph is an append-only event log with periodic snapshots; rollback
re-materializes from the nearest snapshot and moves HEAD back to (snapshot_seq, log_offset).
Nothing is destroyed — events the rolled-past deployment wrote remain in the log.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class MemoryGraphStub:
    store_id = "memory:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    def snapshot(self, coordinate: dict) -> str:
        return digest(f"{coordinate['snapshot_seq']}:{coordinate['log_offset']}")

    def restore(self, coordinate: dict) -> str:
        self.backend.set("memory", {
            "snapshot_seq": coordinate["snapshot_seq"],
            "log_offset": coordinate["log_offset"],
        })
        return self.live_digest()

    def live_digest(self) -> str:
        m = self.backend.get("memory", {})
        return digest(f"{m.get('snapshot_seq')}:{m.get('log_offset')}")

    def live_head(self) -> dict:
        return self.backend.get("memory", {})
