"""Postgres connection helpers (psycopg 3)."""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from agentctl.config import PG_DSN


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a psycopg3 connection (manual transaction control; dict rows)."""
    return psycopg.connect(dsn or PG_DSN, autocommit=False, row_factory=dict_row)


def apply_schema(conn: psycopg.Connection, sql_path: str) -> int:
    """Apply a DDL file. Strips ``--`` comments and splits on ``;`` so it works over
    psycopg3's extended protocol (which rejects multi-statement strings). The schema
    deliberately contains no function bodies / ``$$`` blocks, so naive splitting is safe.
    """
    raw = Path(sql_path).read_text()
    stripped = "\n".join(
        (line[: line.index("--")] if "--" in line else line) for line in raw.splitlines())
    statements = [s.strip() for s in stripped.split(";") if s.strip()]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()
    return len(statements)
