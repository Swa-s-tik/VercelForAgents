# Design — ClickHouse + Grafana telemetry (Workstream 3)

**Status:** done · **Commit:** `feat(telemetry): …`

## Why

The PRD's telemetry boundary (§6) promised a one-env-var switch from the Postgres span buffer to a
ClickHouse warehouse. The MergeTree schema + aggregating materialized views were already written
(`storage/schema_clickhouse.sql`), but there was no exporter, no ClickHouse service, and no
dashboards. 1.0 ships all three — as an **optional** stack so the 5-minute setup stays plain
Postgres.

## The exporter

`agentctl/telemetry/clickhouse_exporter.py::ClickHouseSpanExporter` inserts straight into
`agentctl.otel_spans` over the **ClickHouse HTTP interface** — no OTLP collector dependency, stdlib
`urllib` only (no new pip dep). It mirrors `PostgresSpanExporter`'s discipline: never raises, returns
`SpanExportResult.FAILURE` on error, short-circuits empty batches.

Mapping OTel span → the ClickHouse columns:

| OTel | ClickHouse | note |
|---|---|---|
| `ctx.trace_id` / `span_id` (128/64-bit) | `String` (32/16 hex) | `format(id, '032x')` — BYTEA→hex |
| `start_time` (unix nanos) | `timestamp DateTime64(9)` | via `fromUnixTimestamp64Nano` |
| `end_time - start_time` | `duration_ns UInt64` | |
| `attributes['canary_arm']` | `canary_arm LowCardinality(String)` | lifted to a top-level column so the canary MV populates |
| `attributes` / `resource` | `Map(String,String)` | values stringified |

It can't feed a JSON number straight into `DateTime64(9)`, so it inserts via
`INSERT … SELECT fromUnixTimestamp64Nano(start_nanos), … FROM input(...) FORMAT JSONEachRow` — the
`input()` table function lets the SELECT do the nanosecond conversion.

`_make_exporter("clickhouse")` returns it; **default `TELEMETRY_BACKEND=postgres` is untouched**, so
a plain `docker compose up` never starts ClickHouse and exports to `controlplane.otel_spans` exactly
as before.

## The optional stack

`docker compose --profile telemetry up -d` adds two profiled services (absent from the default `up`):

- **clickhouse** (`clickhouse/clickhouse-server:24.3`): HTTP 8123 + native 9000, creds
  `agentctl/agentctl`, auto-runs `deploy/clickhouse/init/01_schema.sql` (a copy of the warehouse
  DDL) at first start, healthchecked on `/ping`.
- **grafana** (`grafana/grafana:11.1.0`, host **3001**): installs `grafana-clickhouse-datasource`,
  provisions the datasource (`deploy/grafana/provisioning/datasources/clickhouse.yml`) and the
  `agentctl — telemetry overview` dashboard (token usage, latency p50/p95/p99, canary arm
  comparison, throughput — one panel per existing MV).

## Subtlety found (and fixed)

`record_stream_metrics` emits **float** measures (`frames_out=21.0`), which the exporter stringifies
as `"21.0"`. The MVs originally parsed integer measures with `toUInt64OrZero(...)`, which returns 0
on a string with a decimal point — so token/canary/throughput aggregates read **0** while latency
(parsed via `toFloat64OrZero`) worked. Fix: the integer-measure MVs now use
`toUInt64(toFloat64OrZero(...))`, robust to either `"21"` or `"21.0"`. Verified: 5 spans →
token 640/320, canary vA=63 / vB=55 frames, throughput 20480 bytes / 118 frames.

## Verification

```bash
docker compose --profile telemetry up -d clickhouse grafana   # off by default
TELEMETRY_BACKEND=clickhouse python -c "..."                   # emit gateway.stream.metrics spans
curl -u agentctl:agentctl localhost:8123 --data 'SELECT count() FROM agentctl.otel_spans'
#   raw spans + token_usage_1h / canary_arm_1h / latency_1h / throughput_1m all populate
open http://localhost:3001                                     # provisioned dashboard renders
docker compose up -d                                           # no profile -> CH/Grafana absent,
                                                               # TELEMETRY_BACKEND=postgres intact
python -m pytest tests/test_clickhouse_exporter.py -q          # infra-free mapping + no-crash tests
```

## Boundaries / post-1.0

- An OTLP-collector path (`backend=otlp`) still exists for users who prefer a collector; 1.0 adds
  the **native** exporter as the self-contained default for `clickhouse`.
- `deployment_id` is not yet set on stream-metric spans (the MVs group on `''`); wiring it through is
  a small follow-up.
- Postgres→ClickHouse backfill / dual-write is out of scope (the switch is forward-only).
