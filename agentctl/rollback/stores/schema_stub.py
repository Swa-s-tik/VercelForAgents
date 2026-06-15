"""Relational-schema stub: the genuinely-hard case (forward_fix, not reversible).

A down-migration that drops a column DESTROYS data — that is loss, not restore. So this
store REFUSES to auto-run a data-lossy down-migration and signals that a forward-fix is
required. Forward/equal moves (expand-compatible) are allowed. This is why the design
prefers expand-contract: a code rollback then needs no schema rollback at all.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class SchemaMigrationError(Exception):
    """Raised when a rollback would require a data-lossy down-migration."""


class SchemaStoreStub:
    store_id = "appdb:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    def snapshot(self, coordinate: dict) -> str:
        return digest(str(coordinate["migration_version"]))

    def restore(self, coordinate: dict) -> str:
        live = self.backend.get("schema", {}).get("migration_version")
        target = coordinate["migration_version"]
        if live is not None and target < live:
            raise SchemaMigrationError(
                f"down-migration {live} -> {target} would drop data; forward-fix required")
        self.backend.set("schema", {"migration_version": target})
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(str(self.backend.get("schema", {}).get("migration_version")))

    def live_version(self):
        return self.backend.get("schema", {}).get("migration_version")
