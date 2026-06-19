"""Read models for the dashboard - thin SQL over the controlplane system-of-record. Every function
returns plain dicts/lists (psycopg is opened with dict rows), so render.py and the tests never touch
a live connection. Read-only; the only write path is rollback_to_commit, called from app.py."""
from __future__ import annotations

import datetime as _dt
import os
from decimal import Decimal
from pathlib import Path

import psycopg

# decision severity (worst wins when a commit has several suites)
_SEVERITY = {"BLOCK": 3, "INCONCLUSIVE": 2, "INSUFFICIENT_DATA": 1, "ALLOW": 0}


def _jsonable(v):
    """Normalize psycopg row values to JSON-safe types (datetime -> ISO string, Decimal -> float)."""
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def json_snapshot(conn: psycopg.Connection, project_id: str) -> dict:
    """A single JSON-safe view of the control-plane state - the programmatic surface behind both the
    `/api/state` endpoint and `agentctl status --json`, so external tools/CI can consume it."""
    return _jsonable({
        "project_id": project_id,
        "routing_version": live_routing_version(conn, project_id),
        "deployments": list_deployments(conn, project_id),
        "verdicts": verdicts_by_commit(),
        "traffic": stream_telemetry(conn, project_id),
        "routing_history": routing_history(conn, project_id),
        "rollbacks": rollback_history(conn, project_id),
    })


def list_deployments(conn: psycopg.Connection, project_id: str) -> list[dict]:
    """Every deployment with its weight in the LIVE routing table (0 if absent) + canary/shadow flags.
    Newest first. `in_live_table` distinguishes 'serving 0%' from 'not in the live table at all'."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.git_commit_sha, d.status::text AS status, d.created_by, d.created_at,
                   COALESCE(rr.weight, 0)            AS weight,
                   COALESCE(rr.is_canary, false)     AS is_canary,
                   COALESCE(rr.shadow_target, false) AS shadow_target,
                   (rr.id IS NOT NULL)               AS in_live_table
            FROM controlplane.deployments d
            LEFT JOIN controlplane.routing_tables rt
                   ON rt.project_id = d.project_id AND rt.is_live
            LEFT JOIN controlplane.routing_rules rr
                   ON rr.routing_table_id = rt.id AND rr.deployment_id = d.id
            WHERE d.project_id = %s
            ORDER BY d.id DESC
            """,
            [project_id],
        )
        return cur.fetchall()


def live_routing_version(conn: psycopg.Connection, project_id: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM controlplane.routing_tables WHERE project_id=%s AND is_live",
            [project_id])
        row = cur.fetchone()
        return row["version"] if row else None


def deployment_honesty(conn: psycopg.Connection, project_id: str) -> dict[int, dict]:
    """Per deployment: how many captured state mutations are side effects, and how many are
    irreversible. This is the schema-enforced honesty (a side effect can never be 'reversible'),
    surfaced so the UI can warn before a rollback that won't fully undo external actions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id AS deployment_id,
                   count(*) FILTER (WHERE sp.mutation_class = 'side_effect')  AS side_effects,
                   count(*) FILTER (WHERE sp.reversibility = 'irreversible')  AS irreversible,
                   count(*)                                                   AS pointers
            FROM controlplane.deployments d
            JOIN controlplane.checkpoints c    ON c.deployment_id = d.id
            JOIN controlplane.state_pointers sp ON sp.checkpoint_id = c.id
            WHERE d.project_id = %s
            GROUP BY d.id
            """,
            [project_id],
        )
        return {r["deployment_id"]: r for r in cur.fetchall()}


def verdicts_by_commit(db_path: str | None = None) -> dict[str, dict]:
    """Aggregate eval-gate verdict per commit, read from the DuckDB eval store - this is what joins
    the *eval* surface to the *deploy* surface. Returns {} if the store is absent/empty/locked (the
    dashboard then just shows '-'). When a commit has several suites, the overall decision is the
    worst, reported with that suite's win-rate + Wilson CI and the suite count."""
    from agentctl.storage.duckdb_store import DEFAULT_DB

    path = db_path or os.environ.get("AGENTCTL_DUCKDB", DEFAULT_DB)
    if not Path(path).exists():
        return {}
    try:
        import duckdb
        con = duckdb.connect(path, read_only=True)
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT e.commit_sha, e.suite_name, g.decision, g.win_rate, g.wilson_low, g.wilson_high, g.n "
            "FROM eval_run e JOIN gate_result g ON g.run_id = e.run_id"
        ).fetchall()
    except Exception:
        return {}
    finally:
        con.close()

    by: dict[str, dict] = {}
    for commit, suite, decision, wr, lo, hi, n in rows:
        cur = by.get(commit)
        if cur is None:
            by[commit] = {"decision": decision, "suite": suite, "win_rate": wr,
                          "wilson_low": lo, "wilson_high": hi, "n": n, "suites": 1}
            continue
        cur["suites"] += 1
        if _SEVERITY.get(decision, 0) > _SEVERITY.get(cur["decision"], 0):
            cur.update(decision=decision, suite=suite, win_rate=wr, wilson_low=lo, wilson_high=hi, n=n)
    return by


def stream_telemetry(conn: psycopg.Connection, project_id: str, limit: int = 500) -> list[dict]:
    """Aggregate recent gateway stream spans by canary arm - the data plane's live traffic surfaced
    in the UI: streams, frames forwarded, shadow drops, and average latency. Reads the same
    otel_spans the telemetry exporter writes (gateway.stream.metrics). Empty until traffic flows."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH recent AS (
                SELECT attributes, start_unixnano, end_unixnano
                FROM controlplane.otel_spans
                WHERE project_id = %s AND name = 'gateway.stream.metrics'
                ORDER BY start_unixnano DESC
                LIMIT %s
            )
            SELECT COALESCE(attributes->>'canary_arm', '?')               AS arm,
                   count(*)                                               AS streams,
                   COALESCE(sum((attributes->>'measure.frames_out')::float), 0)    AS frames,
                   COALESCE(sum((attributes->>'measure.shadow_dropped')::float), 0) AS shadow_dropped,
                   avg((end_unixnano - start_unixnano) / 1e6)             AS avg_latency_ms
            FROM recent
            GROUP BY attributes->>'canary_arm'
            ORDER BY streams DESC
            """,
            [project_id, limit],
        )
        return cur.fetchall()


def routing_history(conn: psycopg.Connection, project_id: str, limit: int = 12) -> list[dict]:
    """Every routing change as one delivery timeline - each `routing_tables` version carries the
    reason a rollback/canary/promote wrote, plus who and when. Unifies forward and back traffic
    changes that the rollbacks-only history can't show. Includes a one-line weight summary per arm."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT rt.version, rt.is_live, rt.reason, rt.created_by, rt.created_at,
                   COALESCE(string_agg(
                       substr(d.git_commit_sha, 1, 12) || ' ' || (rr.weight / 100)::text || '%%'
                       || CASE WHEN rr.is_canary THEN ' (canary)'
                               WHEN rr.shadow_target THEN ' (shadow)' ELSE '' END,
                       ', ' ORDER BY rr.weight DESC), '') AS arms
            FROM controlplane.routing_tables rt
            LEFT JOIN controlplane.routing_rules rr ON rr.routing_table_id = rt.id
            LEFT JOIN controlplane.deployments d    ON d.id = rr.deployment_id
            WHERE rt.project_id = %s
            GROUP BY rt.id, rt.version, rt.is_live, rt.reason, rt.created_by, rt.created_at
            ORDER BY rt.version DESC
            LIMIT %s
            """,
            [project_id, limit],
        )
        return cur.fetchall()


def rollback_history(conn: psycopg.Connection, project_id: str, limit: int = 10) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.to_commit_sha, r.status::text AS status, r.initiated_by, r.initiated_at,
                   jsonb_array_length(r.unrollbackable) AS unrollbackable_count
            FROM controlplane.rollbacks r
            WHERE r.project_id = %s
            ORDER BY r.initiated_at DESC
            LIMIT %s
            """,
            [project_id, limit],
        )
        return cur.fetchall()
