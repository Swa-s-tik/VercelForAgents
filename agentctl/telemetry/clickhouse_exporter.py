"""Native ClickHouse OTel span exporter (Workstream 3).

Writes spans straight into ``agentctl.otel_spans`` over the ClickHouse HTTP interface — no OTLP
collector required, so the telemetry stack is self-contained. Mirrors the Postgres exporter's
discipline (never crashes the caller; returns FAILURE on error). Maps the OTel span onto the
ClickHouse schema (storage/schema_clickhouse.sql): BYTEA ids -> hex strings, start/end -> a single
``duration_ns`` + a nanosecond ``timestamp``, and lifts ``canary_arm`` to a top-level column so the
aggregating materialized views populate.

It uses ``INSERT … SELECT … FROM input(...)`` so ``fromUnixTimestamp64Nano`` preserves full
nanosecond precision (a JSON number can't be fed straight into DateTime64(9)).
"""
from __future__ import annotations

import json
import urllib.request

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from agentctl.config import (
    CLICKHOUSE_HTTP_ENDPOINT,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_USER,
    DEMO_PROJECT_ID,
)

_INSERT = (
    "INSERT INTO agentctl.otel_spans "
    "(timestamp, trace_id, span_id, parent_span_id, project_id, deployment_id, name, kind, "
    "duration_ns, status_code, canary_arm, attributes, resource) "
    "SELECT fromUnixTimestamp64Nano(start_nanos), trace_id, span_id, parent_span_id, project_id, "
    "deployment_id, name, kind, duration_ns, status_code, canary_arm, attributes, resource "
    "FROM input('start_nanos UInt64, trace_id String, span_id String, parent_span_id String, "
    "project_id String, deployment_id String, name String, kind Int8, duration_ns UInt64, "
    "status_code Int8, canary_arm String, attributes Map(String,String), resource Map(String,String)') "
    "FORMAT JSONEachRow"
)


def _str_map(d) -> dict:
    return {str(k): str(v) for k, v in dict(d or {}).items()}


class ClickHouseSpanExporter(SpanExporter):
    def __init__(self, endpoint: str | None = None, default_project: str = DEMO_PROJECT_ID):
        self.endpoint = (endpoint or CLICKHOUSE_HTTP_ENDPOINT).rstrip("/")
        self.default_project = default_project

    def export(self, spans) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        try:
            rows = []
            for s in spans:
                ctx = s.get_span_context()
                attrs = _str_map(s.attributes)
                res = _str_map(s.resource.attributes) if s.resource else {}
                project = attrs.get("project_id") or res.get("project_id") or self.default_project
                rows.append(json.dumps({
                    "start_nanos": s.start_time,
                    "trace_id": format(ctx.trace_id, "032x"),
                    "span_id": format(ctx.span_id, "016x"),
                    "parent_span_id": format(s.parent.span_id, "016x") if s.parent else "",
                    "project_id": project,
                    "deployment_id": attrs.get("deployment_id", ""),
                    "name": s.name,
                    "kind": int(s.kind.value),
                    "duration_ns": max(0, s.end_time - s.start_time),
                    "status_code": int(s.status.status_code.value),
                    "canary_arm": attrs.get("canary_arm", ""),
                    "attributes": attrs,
                    "resource": res,
                }))
            body = (_INSERT + "\n" + "\n".join(rows)).encode()
            req = urllib.request.Request(
                self.endpoint + "/", data=body, method="POST",
                headers={"X-ClickHouse-User": CLICKHOUSE_USER, "X-ClickHouse-Key": CLICKHOUSE_PASSWORD})
            urllib.request.urlopen(req, timeout=5).read()
            return SpanExportResult.SUCCESS
        except Exception as e:  # never let telemetry crash the caller
            print(f"[telemetry] clickhouse export failed: {e}")
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
