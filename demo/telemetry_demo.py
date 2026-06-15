"""OTel telemetry demo (Phase 5): emit standard spans through the SDK and confirm the
PostgresSpanExporter lands them in controlplane.otel_spans (the local/buffer backend)."""
from __future__ import annotations

from pathlib import Path

import agentctl.rollback as rb
from agentctl.common.db import apply_schema, connect
from agentctl.config import DEMO_PROJECT_ID
from agentctl.telemetry.exporter import make_tracer_provider, record_stream_metrics


def main() -> int:
    conn = connect()
    apply_schema(conn, str(Path(rb.__file__).with_name("schema_postgres.sql")))  # ensures otel_spans

    provider = make_tracer_provider("agentctl-pipeline", backend="postgres")
    tracer = provider.get_tracer("demo")

    with tracer.start_as_current_span("deploy.preview") as s:
        s.set_attribute("commit_sha", "abc123def")
        s.set_attribute("deployment", "preview")
    with tracer.start_as_current_span("eval.gate") as s:
        s.set_attribute("decision", "BLOCK")
        s.set_attribute("suite", "correctness")
        s.set_attribute("wilson_low", 0.32)
    record_stream_metrics(tracer, session_id="sess-1", canary_arm="vA",
                          measures={"frames_out": 12.0, "bytes": 1048576.0, "latency_ms": 3.1})

    provider.force_flush()
    provider.shutdown()

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM controlplane.otel_spans WHERE project_id=%s",
                    [DEMO_PROJECT_ID])
        n = cur.fetchone()["c"]
        cur.execute("SELECT name, kind, attributes FROM controlplane.otel_spans ORDER BY start_unixnano")
        rows = cur.fetchall()
    print(f"otel_spans rows persisted: {n}")
    for r in rows:
        print(f"  span={r['name']:24s} kind={r['kind']}  attrs={r['attributes']}")
    conn.close()
    assert n >= 3, "expected at least 3 spans persisted"
    print("\nOTel -> Postgres exporter: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
