"""Tests for the stdlib OTLP/HTTP exporter: the pure OTLP-JSON builder (against spans from a real
tracer) + export() POSTing to /v1/traces through a captured opener (no collector, no network)."""
from __future__ import annotations

import json

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from agentctl.telemetry import otlp_exporter as ox


def _spans():
    """Emit one span with mixed-typed attributes via a real tracer; return the captured ReadableSpans."""
    mem = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "agentctl", "project_id": "p1"}))
    provider.add_span_processor(SimpleSpanProcessor(mem))
    tracer = provider.get_tracer("agentctl.test", "9.9")
    with tracer.start_as_current_span("gateway.stream.metrics") as s:
        s.set_attribute("canary_arm", "vB")
        s.set_attribute("measure.frames", 21)
        s.set_attribute("measure.p50_ms", 33.0)
        s.set_attribute("label.shadow", True)
    return mem.get_finished_spans()


def test_anyvalue_types():
    assert ox._anyvalue(True) == {"boolValue": True}
    assert ox._anyvalue(21) == {"intValue": "21"}          # int encoded as string in OTLP-JSON
    assert ox._anyvalue(33.0) == {"doubleValue": 33.0}
    assert ox._anyvalue("vB") == {"stringValue": "vB"}
    assert ox._anyvalue([1, 2]) == {"arrayValue": {"values": [{"intValue": "1"}, {"intValue": "2"}]}}


def test_otlp_payload_structure():
    payload = ox.otlp_payload(_spans())
    rs = payload["resourceSpans"]
    assert len(rs) == 1
    # resource carries service.name
    res_attrs = {a["key"]: a["value"] for a in rs[0]["resource"]["attributes"]}
    assert res_attrs["service.name"] == {"stringValue": "agentctl"}

    scope_spans = rs[0]["scopeSpans"]
    assert scope_spans[0]["scope"] == {"name": "agentctl.test", "version": "9.9"}
    span = scope_spans[0]["spans"][0]
    assert span["name"] == "gateway.stream.metrics"
    assert len(span["traceId"]) == 32 and len(span["spanId"]) == 16   # hex ids
    assert int(span["startTimeUnixNano"]) > 0 and int(span["endTimeUnixNano"]) > 0

    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["canary_arm"] == {"stringValue": "vB"}
    assert attrs["measure.frames"] == {"intValue": "21"}
    assert attrs["measure.p50_ms"] == {"doubleValue": 33.0}
    assert attrs["label.shadow"] == {"boolValue": True}


def test_empty_payload():
    assert ox.otlp_payload([]) == {"resourceSpans": []}


def test_export_posts_to_v1_traces():
    sink: dict = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        sink["url"] = req.full_url
        sink["method"] = req.get_method()
        sink["ctype"] = dict((k.lower(), v) for k, v in req.header_items()).get("content-type")
        sink["body"] = json.loads(req.data.decode())
        return FakeResp()

    exp = ox.OTLPHttpSpanExporter(endpoint="http://collector:4318/v1/traces")
    orig = ox.urllib.request.urlopen
    ox.urllib.request.urlopen = fake_urlopen
    try:
        result = exp.export(_spans())
    finally:
        ox.urllib.request.urlopen = orig

    assert result == SpanExportResult.SUCCESS
    assert sink["url"] == "http://collector:4318/v1/traces"
    assert sink["method"] == "POST" and sink["ctype"] == "application/json"
    assert sink["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "gateway.stream.metrics"


def test_export_failure_never_raises():
    def boom(req, timeout=None):
        raise OSError("collector down")

    exp = ox.OTLPHttpSpanExporter()
    orig = ox.urllib.request.urlopen
    ox.urllib.request.urlopen = boom
    try:
        assert exp.export(_spans()) == SpanExportResult.FAILURE   # returned, not raised
    finally:
        ox.urllib.request.urlopen = orig

    assert exp.export([]) == SpanExportResult.SUCCESS  # empty is a no-op success
