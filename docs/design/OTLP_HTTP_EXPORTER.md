# Design - OTLP/HTTP span exporter (stdlib, no extra dep)

**Status:** done · **Commit:** `feat(telemetry): stdlib OTLP/HTTP exporter (TELEMETRY_BACKEND=otlp)`

## Why

The roadmap listed "OTLP-collector telemetry path alongside the native ClickHouse exporter." The repo
already had an `otlp` backend, but it imported the heavy gRPC `opentelemetry-exporter-otlp` package
and fell back to console when it was absent - so out of the box, `TELEMETRY_BACKEND=otlp` exported
nothing useful. This makes it real with **zero new dependency**: the same OTel spans are shipped as
**OTLP-JSON over HTTP** to any collector's `/v1/traces`, the same dependency-light discipline as the
native ClickHouse exporter (stdlib `urllib`, never crashes the caller).

This is the open, vendor-neutral path: point it at an OpenTelemetry Collector, Tempo, Jaeger,
Honeycomb, Grafana Cloud - anything that speaks OTLP/HTTP - while ClickHouse stays the self-contained
warehouse option.

## What it does

- `agentctl/telemetry/otlp_exporter.py`:
  - `otlp_payload(spans)` - **pure** builder: groups spans by instrumentation scope under the shared
    resource and emits an OTLP `ExportTraceServiceRequest` (`resourceSpans -> scopeSpans -> spans`).
    Trace/span ids are lowercase hex (32/16), timestamps are stringified nanos, and attributes map to
    OTLP `AnyValue` with the correct typing (bool before int since `bool` is an `int` subclass; int as
    a JSON string per the OTLP-JSON spec).
  - `OTLPHttpSpanExporter(SpanExporter)` - POSTs the payload to `OTLP_HTTP_ENDPOINT`
    (`AGENTCTL_OTLP_ENDPOINT`, default `http://localhost:4318/v1/traces`); returns `FAILURE` on any
    error, never raises into the caller.
- `telemetry/exporter.py::_make_exporter`: `otlp` now returns the stdlib HTTP exporter; the previous
  gRPC path is preserved behind `otlp-grpc` for anyone who wants it. Default backend stays `postgres`.

## Boundaries (honest)

- It is a `SimpleSpanProcessor`/synchronous exporter like the others here - one POST per export batch,
  no ret/queue/backoff. Fine for the gateway's metric spans; a high-volume deployment would want the
  batching processor (an env knob, not a redesign).
- OTLP-JSON only (not protobuf-over-HTTP). Collectors accept JSON on `/v1/traces`; protobuf would be a
  marginal efficiency gain for a new dependency, which defeats the point.

## Verified

- `tests/test_otlp_exporter.py` - the `AnyValue` typing, the full `otlp_payload` structure (built from
  spans emitted by a **real** tracer: resource `service.name`, scope name/version, hex ids, nanos,
  typed attributes), the empty-batch no-op, `export()` POSTing to `/v1/traces` (URL/method/content-type
  + body asserted through a captured opener), and that a collector being down returns `FAILURE` instead
  of raising.
- Wiring: `_make_exporter('otlp')` returns `OTLPHttpSpanExporter`. Full suite 144 passed.
