"""FastAPI dependency that authenticates + authorizes a request (Workstream 2).

Reads the key from ``X-API-Key`` or ``Authorization: Bearer``. With no key the request resolves to
the bootstrap owner (backward-compat) unless ``AGENTCTL_REQUIRE_KEY=1``, which makes a key
mandatory. No DB connection is opened on the no-key path.
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException

from agentctl.auth.principal import AuthError, Principal, resolve_principal


def _extract(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def principal_dep(min_role: str = "viewer"):
    """Build a FastAPI dependency enforcing at least ``min_role``."""
    def dep(authorization: str | None = Header(None),
            x_api_key: str | None = Header(None, alias="X-API-Key")) -> Principal:
        key = _extract(authorization, x_api_key)
        if key:
            from agentctl.common.db import connect
            conn = connect()
            try:
                principal = resolve_principal(conn, key)
            except AuthError as e:
                raise HTTPException(401, str(e))
            finally:
                conn.close()
        else:
            if os.environ.get("AGENTCTL_REQUIRE_KEY") == "1":
                raise HTTPException(401, "API key required (AGENTCTL_REQUIRE_KEY=1)")
            principal = resolve_principal(None, None)  # bootstrap owner; no DB access
        try:
            principal.require(min_role)
        except AuthError as e:
            raise HTTPException(403, str(e))
        return principal
    return dep
