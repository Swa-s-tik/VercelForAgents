"""`agentctl auth ...` — API key management (Workstream 2).

Key creation/revocation require the owner role; listing requires viewer. The caller authenticates
with --api-key / AGENTCTL_API_KEY (absent -> bootstrap owner, for local/zero-config use).
"""
from __future__ import annotations

import os
import sys

from agentctl.auth.principal import AuthError, resolve_principal


def _caller_key(args) -> str | None:
    return getattr(args, "api_key", None) or os.environ.get("AGENTCTL_API_KEY")


def _cmd_create_key(args) -> int:
    from agentctl.auth.keys import generate_key
    from agentctl.common.db import connect
    conn = connect()
    try:
        principal = resolve_principal(conn, _caller_key(args)).require("owner")
        secret, prefix, key_hash = generate_key()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO controlplane.api_keys (project_id,name,key_prefix,key_hash,role) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                [principal.project_id, args.name, prefix, key_hash, args.role])
            kid = cur.fetchone()["id"]
        conn.commit()
    except AuthError as e:
        print(f"denied: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"created key (id={kid}, role={args.role}, project={principal.project_id})")
    print(f"  secret (shown once, store it now): {secret}")
    return 0


def _cmd_list_keys(args) -> int:
    from agentctl.common.db import connect
    conn = connect()
    try:
        principal = resolve_principal(conn, _caller_key(args)).require("viewer")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, key_prefix, role, created_at, revoked_at FROM controlplane.api_keys "
                "WHERE project_id=%s ORDER BY created_at", [principal.project_id])
            rows = cur.fetchall()
    except AuthError as e:
        print(f"denied: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"api keys for project {principal.project_id}:")
    for r in rows:
        state = "REVOKED" if r["revoked_at"] else "active"
        print(f"  {r['key_prefix']}…  {r['role']:9s} {state:8s} {r['name']}")
    return 0


def _cmd_revoke_key(args) -> int:
    from agentctl.common.db import connect
    conn = connect()
    try:
        principal = resolve_principal(conn, _caller_key(args)).require("owner")
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE controlplane.api_keys SET revoked_at=now() "
                "WHERE project_id=%s AND key_prefix=%s AND revoked_at IS NULL RETURNING id",
                [principal.project_id, args.prefix])
            hit = cur.fetchall()
        conn.commit()
    except AuthError as e:
        print(f"denied: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"revoked {len(hit)} key(s) with prefix {args.prefix!r}")
    return 0 if hit else 1


def add_auth_parsers(sub) -> None:
    a = sub.add_parser("auth", help="API key management (Workstream 2 / RBAC)")
    asub = a.add_subparsers(dest="authcmd", required=True)

    ck = asub.add_parser("create-key", help="create an API key (owner only)")
    ck.add_argument("--name", default="key")
    ck.add_argument("--role", default="developer", choices=["viewer", "developer", "admin", "owner"])
    ck.add_argument("--api-key", default=None, help="caller key (else AGENTCTL_API_KEY / bootstrap)")
    ck.set_defaults(func=_cmd_create_key)

    lk = asub.add_parser("list-keys", help="list API keys for your project (viewer+)")
    lk.add_argument("--api-key", default=None)
    lk.set_defaults(func=_cmd_list_keys)

    rk = asub.add_parser("revoke-key", help="revoke a key by prefix (owner only)")
    rk.add_argument("prefix", help="the key_prefix shown by list-keys")
    rk.add_argument("--api-key", default=None)
    rk.set_defaults(func=_cmd_revoke_key)
