"""API-key generation + hashing (Workstream 2).

Keys are ``actl_`` + 48 hex chars. Only the sha256 hash is ever stored (``api_keys.key_hash``);
the secret is shown to the operator exactly once at creation. ``key_prefix`` (first 12 chars) is
safe to log/display.
"""
from __future__ import annotations

import hashlib
import secrets

PREFIX = "actl_"
_BODY_BYTES = 24  # -> 48 hex chars

# The documented zero-config bootstrap key (seeded by schema_postgres.sql with role owner). Lets
# `agentctl push` and the demo work with no key while still flowing through the real auth path.
BOOTSTRAP_KEY = "actl_dev_bootstrap_0000000000000000"


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def key_prefix(secret: str) -> str:
    return secret[:12]


def generate_key() -> tuple[str, str, str]:
    """Return (secret, prefix, hash). Persist prefix+hash; hand the secret to the user once."""
    secret = PREFIX + secrets.token_hex(_BODY_BYTES)
    return secret, key_prefix(secret), hash_key(secret)
