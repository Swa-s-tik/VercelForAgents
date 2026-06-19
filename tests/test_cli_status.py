"""Tests for `agentctl status` - the terminal summary. Builds a snapshot over a seeded Postgres and
renders it to a recording console, asserting the deploy + traffic surfaces show up. Reuses the
dashboard queries, so this also guards that the CLI and web surfaces stay in sync."""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

import agentctl.rollback as rbpkg
from agentctl.cli import status as st
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID

_SCHEMA = str(Path(rbpkg.__file__).with_name("schema_postgres.sql"))


def _seeded():
    try:
        conn = connect()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no Postgres: {e}")
    apply_schema(conn, _SCHEMA)
    from agentctl.rollback.seed import seed
    seed(conn)
    return conn


def test_status_renders_deployments_and_traffic():
    from psycopg.types.json import Json
    conn = _seeded()
    try:
        with conn.cursor() as cur:  # one gateway stream span so the traffic panel has data
            cur.execute(
                "INSERT INTO controlplane.otel_spans (trace_id, span_id, project_id, name, kind, "
                "start_unixnano, end_unixnano, status_code, attributes) "
                "VALUES (%s,%s,%s,'gateway.stream.metrics',2,0,%s,0,%s)",
                [b"\x01" * 16, b"\x01" * 8, DEMO_PROJECT_ID, 35_000_000,
                 Json({"canary_arm": "vB", "measure.frames_out": 21.0, "measure.shadow_dropped": 0.0})])
        conn.commit()

        snap = st.build_snapshot(conn, DEMO_PROJECT_ID)
        assert len(snap["deployments"]) >= 2
        assert snap["traffic"], "expected the inserted stream span to show as traffic"

        console = Console(record=True, width=160)
        st.render(snap, DEMO_PROJECT_ID, console)
        text = console.export_text()
        assert "Deployments" in text
        assert "bbbb2222bbbb" in text          # the live deployment commit
        assert "Live traffic" in text and "vB" in text
        assert "Delivery timeline" in text     # routing-change history is shown
    finally:
        conn.close()


def test_verdict_and_arm_formatters():
    assert "ALLOW" in st._verdict_str({"decision": "ALLOW", "suites": 1})
    assert st._verdict_str(None) == "[dim]-[/]"
    assert "100%" in st._arm_str({"in_live_table": True, "weight": 10000,
                                  "is_canary": False, "shadow_target": False})
    assert st._arm_str({"in_live_table": False, "weight": 0,
                        "is_canary": False, "shadow_target": False}) == "[dim]-[/]"
