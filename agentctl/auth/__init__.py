"""Multi-tenant RBAC: API keys, Principal resolution, and enforcement seams (Workstream 2)."""
from agentctl.auth.keys import BOOTSTRAP_KEY, generate_key, hash_key, key_prefix
from agentctl.auth.principal import (
    BOOTSTRAP_PRINCIPAL,
    ROLE_RANK,
    AuthError,
    Principal,
    resolve_principal,
)

__all__ = [
    "AuthError", "Principal", "BOOTSTRAP_PRINCIPAL", "ROLE_RANK", "resolve_principal",
    "BOOTSTRAP_KEY", "generate_key", "hash_key", "key_prefix",
]
