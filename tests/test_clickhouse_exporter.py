"""ClickHouse span exporter mapping (Workstream 3). Infra-free: the HTTP POST is captured."""
from __future__ import annotations

import json
import urllib.request

from agentctl.telemetry.clickhouse_exporter import ClickHouseSpanExporter
from agentctl.telemetry.exporter import _make_exporter, make_tracer_provider, record_stream_metrics


def test_make_exporter_selects_clickhouse():
    assert isinstance(_make_exporter("clickhouse"), ClickHouseSpanExporter)
    # default backend must NOT be clickhouse (the zero-config path stays postgres)
    from agentctl.telemetry.exporter import PostgresSpanExporter
    assert isinstance(_make_exporter("postgres"), PostgresSpanExporter)


def test_row_mapping(monkeypatch):
    captured = {}

    class _Resp:
        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):
        captured["body"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    prov = make_tracer_provider("t", backend="clickhouse")  # SimpleSpanProcessor -> sync export
    tr = prov.get_tracer("t")
    record_stream_metrics(tr, session_id="s1", canary_arm="vB",
                          measures={"frames_out": 21.0, "latency_ms": 33.0})
    prov.force_flush(); prov.shutdown()

    body = captured["body"].decode()
    assert body.startswith("INSERT INTO agentctl.otel_spans")
    assert "fromUnixTimestamp64Nano" in body          # nanosecond-precise timestamp
    row = json.loads(body.strip().splitlines()[-1])    # the JSONEachRow line
    assert row["name"] == "gateway.stream.metrics"
    assert row["canary_arm"] == "vB"                   # lifted to a top-level column for the MVs
    assert len(row["trace_id"]) == 32 and len(row["span_id"]) == 16   # BYTEA -> hex
    assert row["duration_ns"] >= 0
    assert row["attributes"]["measure.frames_out"] == "21.0"
    # authenticated insert
    assert "x-clickhouse-user" in captured["headers"]


def test_export_never_raises(monkeypatch):
    """A telemetry backend outage must never crash the caller (returns FAILURE)."""
    def boom(req, timeout=None):
        raise OSError("clickhouse down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    from opentelemetry.sdk.trace.export import SpanExportResult
    exp = ClickHouseSpanExporter()
    assert exp.export([]) == SpanExportResult.SUCCESS      # empty batch is fine
    prov = make_tracer_provider("t", backend="clickhouse")
    tr = prov.get_tracer("t")
    with tr.start_as_current_span("x"):                   # export fails internally, no raise
        pass
    prov.shutdown()
