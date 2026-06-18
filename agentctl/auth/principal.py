"""Principal resolution + RBAC roles (Workstream 2).

A Principal is the authenticated identity behind a request: which project it acts on and at what
role. ``resolve_principal(conn, api_key)`` is the single chokepoint every surface (HTTP, gRPC, CLI)
uses. The backward-compat keystone: ``api_key=None`` returns the bootstrap owner Principal scoped to
``DEMO_PROJECT_ID`` (a real seeded row), so the zero-config demo and existing tests are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from agentctl.config import DEMO_PROJECT_ID

# higher = more privilege; require(min) checks rank >= ROLE_RANK[min]
ROLE_RANK = {"viewer": 0, "developer": 1, "admin": 2, "owner": 3}


class AuthError(Exception):
    """Authentication failed (bad/revoked key) or authorization denied (insufficient role)."""


@dataclass(frozen=True)
class Principal:
    project_id: str
    role: str
    key_id: str | None = None
    name: str = "bootstrap"

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.role, -1)

    def require(self, min_role: str) -> "Principal":
        if self.rank < ROLE_RANK[min_role]:
            raise AuthError(f"role '{self.role}' is below required '{min_role}'")
        return self


# the no-key default: owner of the historic demo project (seeded by schema_postgres.sql).
BOOTSTRAP_PRINCIPAL = Principal(project_id=DEMO_PROJECT_ID, role="owner", key_id=None,
                                name="bootstrap")


def resolve_principal(conn, api_key: str | None) -> Principal:
    """Resolve an API key to a Principal. ``None`` -> bootstrap owner (no DB access). A non-empty
    key is looked up by sha256 hash; unknown/revoked -> AuthError."""
    if not api_key:
        return BOOTSTRAP_PRINCIPAL
    from agentctl.auth.keys import hash_key
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, project_id, role, name FROM controlplane.api_keys "
            "WHERE key_hash=%s AND revoked_at IS NULL",
            [hash_key(api_key)])
        row = cur.fetchone()
    if not row:
        raise AuthError("invalid or revoked API key")
    return Principal(project_id=str(row["project_id"]), role=str(row["role"]),
                     key_id=str(row["id"]), name=row["name"] or "")
