"""Relational schema - functional mock with a migration history (Phase 8).

The genuinely-hard case. A down-migration that drops a column DESTROYS data, so this store
REFUSES to auto-run a data-lossy down-migration and signals a forward-fix is required.
Forward/equal moves (expand-compatible) are allowed and recorded in the migration history.
"""
from __future__ import annotations

from agentctl.rollback.stores.base import JsonBackend, digest


class SchemaMigrationError(Exception):
    """Raised when a rollback would require a data-lossy down-migration."""


class SchemaStoreStub:
    store_id = "appdb:demo"

    def __init__(self, backend: JsonBackend | None = None):
        self.backend = backend or JsonBackend()

    def _schema(self):
        st = self.backend.load()
        return st, st.setdefault("schema", {"migration_version": None, "history": []})

    def migrate(self, version: int) -> None:
        st, s = self._schema()
        s["migration_version"] = version
        s.setdefault("history", []).append(version)
        self.backend.save(st)

    set_version = migrate  # alias

    # ---- StateStore interface (unchanged semantics) ------------------------
    def snapshot(self, coordinate: dict) -> str:
        return digest(str(coordinate["migration_version"]))

    def restore(self, coordinate: dict) -> str:
        live = self.backend.get("schema", {}).get("migration_version")
        target = coordinate["migration_version"]
        if live is not None and target < live:
            raise SchemaMigrationError(
                f"down-migration {live} -> {target} would drop data; forward-fix required")
        self.migrate(target)
        return self.live_digest()

    def live_digest(self) -> str:
        return digest(str(self.live_version()))

    def live_version(self):
        return self.backend.get("schema", {}).get("migration_version")

    def history(self) -> list:
        return self.backend.get("schema", {}).get("history", [])
