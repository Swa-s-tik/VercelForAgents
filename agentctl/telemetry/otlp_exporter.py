"""OTLP/HTTP span exporter - stdlib only (urllib), no opentelemetry-exporter-otlp dependency.

The repo already had an `otlp` backend, but it required the heavy gRPC OTLP package. This exports the
same OTel spans as **OTLP-JSON over HTTP** to any collector's `/v1/traces` (otelcol, Tempo, Jaeger,
Honeycomb, Grafana Cloud, ...), keeping the telemetry stack dependency-light - the same discipline as
the ClickHouse exporter (stdlib HTTP, never crashes the caller, FAILURE on error).

The payload builder `otlp_payload()` is pure (spans in, OTLP dict out), so it is unit-tested without a
collector; only `export()` does I/O.
"""
from __future__ import annotations

import json
import urllib.request

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from agentctl.config import OTLP_HTTP_ENDPOINT


def _anyvalue(v) -> dict:
    """Map a Python attribute value to an OTLP AnyValue (bool before int: bool is an int subclass;
    OTLP encodes intValue as a string in JSON)."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_anyvalue(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs(d) -> list[dict]:
    return [{"key": str(k), "value": _anyvalue(v)} for k, v in dict(d or {}).items()]


def _span_json(s) -> dict:
    ctx = s.get_span_context()
    out = {
        "traceId": format(ctx.trace_id, "032x"),
        "spanId": format(ctx.span_id, "016x"),
        "name": s.name,
        "kind": int(s.kind.value),
        "startTimeUnixNano": str(s.start_time),
        "endTimeUnixNano": str(s.end_time),
        "attributes": _attrs(s.attributes),
        "status": {"code": int(s.status.status_code.value)},
    }
    if s.parent is not None:
        out["parentSpanId"] = format(s.parent.span_id, "016x")
    return out


def otlp_payload(spans) -> dict:
    """Build an OTLP ExportTraceServiceRequest (resourceSpans -> scopeSpans -> spans), grouping by
    instrumentation scope under the shared resource."""
    if not spans:
        return {"resourceSpans": []}
    res = spans[0].resource
    resource = {"attributes": _attrs(res.attributes if res else {})}

    by_scope: dict[tuple, dict] = {}
    for s in spans:
        scope = getattr(s, "instrumentation_scope", None)
        key = (scope.name if scope else "", scope.version if scope else "")
        bucket = by_scope.setdefault(key, {"scope": {"name": key[0], "version": key[1] or ""},
                                           "spans": []})
        bucket["spans"].append(_span_json(s))
    return {"resourceSpans": [{"resource": resource, "scopeSpans": list(by_scope.values())}]}


class OTLPHttpSpanExporter(SpanExporter):
    def __init__(self, endpoint: str | None = None):
        self.endpoint = (endpoint or OTLP_HTTP_ENDPOINT)

    def export(self, spans) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS
        try:
            req = urllib.request.Request(
                self.endpoint, data=json.dumps(otlp_payload(spans)).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10):
                return SpanExportResult.SUCCESS
        except Exception as e:  # never let telemetry crash the caller
            print(f"[telemetry] OTLP/HTTP export failed: {e}")
            return SpanExportResult.FAILURE

    def shutdown(self):  # nothing to close (stateless HTTP)
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
