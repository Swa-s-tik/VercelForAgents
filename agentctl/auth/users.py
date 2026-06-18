"""User + role-binding management (post-1.0 RBAC).

A user is an org member; a role_binding grants that user a role on a project. An API key may belong
to a user, in which case its effective role is the user's binding (resolved in
``principal.resolve_principal``). Standalone keys keep the 1.0 role-per-key behavior.
"""
from __future__ import annotations


def org_for_project(conn, project_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT org_id FROM controlplane.projects WHERE id=%s", [project_id])
        row = cur.fetchone()
    if not row:
        raise ValueError(f"no such project: {project_id}")
    return str(row["org_id"])


def create_user(conn, org_id: str, email: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO controlplane.users (org_id, email) VALUES (%s,%s) "
            "ON CONFLICT (org_id, email) DO UPDATE SET email=EXCLUDED.email RETURNING id",
            [org_id, email])
        return str(cur.fetchone()["id"])


def user_by_email(conn, org_id: str, email: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM controlplane.users WHERE org_id=%s AND email=%s", [org_id, email])
        row = cur.fetchone()
    return str(row["id"]) if row else None


def bind_role(conn, user_id: str, project_id: str, role: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO controlplane.role_bindings (user_id, project_id, role) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, project_id) DO UPDATE SET role=EXCLUDED.role",
            [user_id, project_id, role])


def list_users(conn, project_id: str) -> list:
    """Users in the project's org, with their role on this project (NULL if no binding)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.email, rb.role FROM controlplane.users u "
            "JOIN controlplane.projects p ON p.org_id = u.org_id AND p.id = %s "
            "LEFT JOIN controlplane.role_bindings rb ON rb.user_id = u.id AND rb.project_id = %s "
            "ORDER BY u.email", [project_id, project_id])
        return cur.fetchall()
