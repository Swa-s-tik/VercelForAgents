"""Vector-store stub: commit-scoped namespaces + an alias swap on restore (reversible).

Real strategy: each deployment writes into a namespace keyed by commit; rollback repoints
the project's live alias at the target commit's namespace (O(1), atomic at the store).
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class VectorStoreStub:
    store_id = "vector:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    def snapshot(self, coordinate: dict) -> str:
        return digest(coordinate["namespace"])

    def restore(self, coordinate: dict) -> str:
        # idempotent alias swap: setting the live alias to X twice == X.
        self.backend.set("vector", {"alias_namespace": coordinate["namespace"]})
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(self.backend.get("vector", {}).get("alias_namespace", ""))

    def live_namespace(self) -> str | None:
        return self.backend.get("vector", {}).get("alias_namespace")
