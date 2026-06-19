"""make_tracer_provider picks SimpleSpanProcessor by default and BatchSpanProcessor when batching is
requested (arg or AGENTCTL_TELEMETRY_BATCH=1), so a busy gateway can flush spans off the hot path."""
from __future__ import annotations

from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from agentctl.telemetry.exporter import make_tracer_provider


def _processor(provider):
    # the active span processor is a SynchronousMultiSpanProcessor wrapping our single processor
    sp = provider._active_span_processor
    inner = getattr(sp, "_span_processors", None)
    return (inner[0] if inner else sp)


def test_default_is_simple_processor():
    p = make_tracer_provider(backend="console", batch=False)
    assert isinstance(_processor(p), SimpleSpanProcessor)


def test_batch_arg_selects_batch_processor():
    p = make_tracer_provider(backend="console", batch=True)
    assert isinstance(_processor(p), BatchSpanProcessor)


def test_batch_env_selects_batch_processor(monkeypatch):
    monkeypatch.setenv("AGENTCTL_TELEMETRY_BATCH", "1")
    p = make_tracer_provider(backend="console")  # batch unset -> read from env
    assert isinstance(_processor(p), BatchSpanProcessor)
