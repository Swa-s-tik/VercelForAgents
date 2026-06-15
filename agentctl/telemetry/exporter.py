"""OpenTelemetry exporter backends (Phase 5).

Standard OTel spans, with a backend chosen by env var ``TELEMETRY_BACKEND``:
  * ``postgres`` (default): PostgresSpanExporter writes spans into controlplane.otel_spans
    (the append-only buffer — same OTel shape that maps 1:1 to the ClickHouse warehouse).
  * ``console``: ConsoleSpanExporter (local debugging).
  * ``otlp`` / ``clickhouse``: OTLP exporter to a collector (prod warehouse path), if the
    opentelemetry-exporter-otlp package is installed; else falls back to console.

The span schema (names, attribute keys) is the boundary contract with the prod telemetry
engine — flip one env var, no schema change. gate/gateway code emits spans through the
standard SDK; only the exporter wiring differs.
"""
from __future__ import annotations

import psycopg
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from psycopg.types.json import Json

from agentctl.config import DEMO_PROJECT_ID, PG_DSN, TELEMETRY_BACKEND


def _jsonable(d) -> dict:
    out = {}
    for k, v in dict(d or {}).items():
        out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else \
            list(v) if isinstance(v, (list, tuple)) else str(v)
    return out


class PostgresSpanExporter(SpanExporter):
    """Writes OTel spans into controlplane.otel_spans (the local/buffer backend)."""

    def __init__(self, dsn: str = PG_DSN, default_project: str = DEMO_PROJECT_ID):
        self.dsn = dsn
        self.default_project = default_project
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, autocommit=True)
        return self._conn

    def export(self, spans) -> SpanExportResult:
        try:
            cur = self._connection().cursor()
            for s in spans:
                ctx = s.get_span_context()
                attrs = _jsonable(s.attributes)
                res = _jsonable(s.resource.attributes) if s.resource else {}
                project = attrs.get("project_id") or res.get("project_id") or self.default_project
                parent = s.parent.span_id.to_bytes(8, "big") if s.parent else None
                scope = {}
                if getattr(s, "instrumentation_scope", None):
                    scope = {"name": s.instrumentation_scope.name,
                             "version": s.instrumentation_scope.version}
                cur.execute(
                    """INSERT INTO controlplane.otel_spans
                       (trace_id, span_id, parent_span_id, project_id, name, kind,
                        start_unixnano, end_unixnano, status_code, attributes, resource, scope)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (trace_id, span_id) DO NOTHING""",
                    [ctx.trace_id.to_bytes(16, "big"), ctx.span_id.to_bytes(8, "big"), parent,
                     project, s.name, int(s.kind.value), s.start_time, s.end_time,
                     int(s.status.status_code.value), Json(attrs), Json(res), Json(scope)])
            return SpanExportResult.SUCCESS
        except Exception as e:  # never let telemetry crash the caller
            print(f"[telemetry] span export failed: {e}")
            return SpanExportResult.FAILURE

    def shutdown(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _make_exporter(backend: str) -> SpanExporter:
    if backend == "postgres":
        return PostgresSpanExporter()
    if backend in ("otlp", "clickhouse"):
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            return OTLPSpanExporter()
        except Exception:
            print(f"[telemetry] OTLP exporter unavailable; falling back to console "
                  f"(install opentelemetry-exporter-otlp for backend={backend})")
            return ConsoleSpanExporter()
    return ConsoleSpanExporter()


def make_tracer_provider(service_name: str = "agentctl", project_id: str = DEMO_PROJECT_ID,
                         backend: str | None = None) -> TracerProvider:
    backend = backend or TELEMETRY_BACKEND
    resource = Resource.create({"service.name": service_name, "project_id": project_id})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(_make_exporter(backend)))
    return provider


def record_stream_metrics(tracer, *, session_id: str, canary_arm: str, measures: dict,
                          labels: dict | None = None, project_id: str = DEMO_PROJECT_ID) -> None:
    """Mirror gateway stream metrics (frames/bytes/latency) as an OTel span's attributes."""
    with tracer.start_as_current_span("gateway.stream.metrics") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("session_id", session_id)
        span.set_attribute("canary_arm", canary_arm)
        for k, v in measures.items():
            span.set_attribute(f"measure.{k}", v)
        for k, v in (labels or {}).items():
            span.set_attribute(f"label.{k}", str(v))
