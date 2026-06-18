-- ClickHouse production telemetry warehouse (Phase 6).
--
-- The env-toggled production backend for heavy OTel logs. Local/preview uses DuckDB
-- (eval traces) + Postgres.otel_spans (short buffer); at scale, set TELEMETRY_BACKEND=clickhouse
-- and OTEL spans flow OTLP collector -> ClickHouse here. The raw span shape MIRRORS
-- controlplane.otel_spans (Postgres) 1:1, so promotion needs no app change — only the exporter
-- wiring switches (agentctl/telemetry/exporter.py). Aggregates are maintained by incremental
-- materialized views so dashboards never scan the raw firehose.
--
-- NOTE: not executed in the local prototype (no ClickHouse server; commented in
-- deploy/docker-compose.yml). This is the production DDL + the toggle contract.

CREATE DATABASE IF NOT EXISTS agentctl;

-- ============================================================================
-- 1. Raw spans (the firehose). MergeTree, partitioned by day, TTL'd.
-- ============================================================================
CREATE TABLE IF NOT EXISTS agentctl.otel_spans
(
    timestamp       DateTime64(9)            CODEC(Delta, ZSTD(1)),
    trace_id        String                   CODEC(ZSTD(1)),
    span_id         String                   CODEC(ZSTD(1)),
    parent_span_id  String                   CODEC(ZSTD(1)),
    project_id      LowCardinality(String),
    deployment_id   String                   CODEC(ZSTD(1)),
    name            LowCardinality(String),
    kind            Int8,
    duration_ns     UInt64                   CODEC(T64, ZSTD(1)),
    status_code     Int8,
    canary_arm      LowCardinality(String),
    attributes      Map(String, String)      CODEC(ZSTD(1)),
    resource        Map(String, String)      CODEC(ZSTD(1)),
    INDEX idx_trace  trace_id     TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_deploy deployment_id TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (project_id, name, toStartOfHour(timestamp), trace_id)
TTL toDate(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

-- ============================================================================
-- 2. Token aggregation matrix (per deployment / hour) — AggregatingMergeTree.
-- ============================================================================
CREATE TABLE IF NOT EXISTS agentctl.token_usage_1h
(
    project_id        LowCardinality(String),
    deployment_id     String,
    hour              DateTime,
    prompt_tokens     SimpleAggregateFunction(sum, UInt64),
    completion_tokens SimpleAggregateFunction(sum, UInt64),
    requests          SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toDate(hour)
ORDER BY (project_id, deployment_id, hour);

CREATE MATERIALIZED VIEW IF NOT EXISTS agentctl.token_usage_1h_mv
TO agentctl.token_usage_1h AS
SELECT
    project_id,
    deployment_id,
    toStartOfHour(timestamp)                                   AS hour,
    toUInt64(toFloat64OrZero(attributes['measure.prompt_tokens']))        AS prompt_tokens,
    toUInt64(toFloat64OrZero(attributes['measure.completion_tokens']))    AS completion_tokens,
    toUInt64(1)                                                AS requests
FROM agentctl.otel_spans
WHERE name = 'gateway.stream.metrics';

-- ============================================================================
-- 3. Latency quantiles (per deployment / arm / hour) — t-digest aggregate state.
-- ============================================================================
CREATE TABLE IF NOT EXISTS agentctl.latency_1h
(
    project_id    LowCardinality(String),
    deployment_id String,
    canary_arm    LowCardinality(String),
    hour          DateTime,
    latency_ms    AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Float64),
    samples       SimpleAggregateFunction(sum, UInt64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toDate(hour)
ORDER BY (project_id, deployment_id, canary_arm, hour);

CREATE MATERIALIZED VIEW IF NOT EXISTS agentctl.latency_1h_mv
TO agentctl.latency_1h AS
SELECT
    project_id,
    deployment_id,
    canary_arm,
    toStartOfHour(timestamp)                                                          AS hour,
    quantilesTDigestState(0.5, 0.95, 0.99)(toFloat64OrZero(attributes['measure.latency_ms'])) AS latency_ms,
    toUInt64(1)                                                                       AS samples
FROM agentctl.otel_spans
WHERE name = 'gateway.stream.metrics'
GROUP BY project_id, deployment_id, canary_arm, hour;
-- read back with: quantilesTDigestMerge(0.5,0.95,0.99)(latency_ms)

-- ============================================================================
-- 4. Canary comparison matrix (per arm / hour) — SummingMergeTree.
-- ============================================================================
CREATE TABLE IF NOT EXISTS agentctl.canary_arm_1h
(
    project_id    LowCardinality(String),
    canary_arm    LowCardinality(String),
    hour          DateTime,
    frames_out    UInt64,
    shadow_sent   UInt64,
    shadow_dropped UInt64,
    sessions      UInt64
)
ENGINE = SummingMergeTree
PARTITION BY toDate(hour)
ORDER BY (project_id, canary_arm, hour);

CREATE MATERIALIZED VIEW IF NOT EXISTS agentctl.canary_arm_1h_mv
TO agentctl.canary_arm_1h AS
SELECT
    project_id,
    canary_arm,
    toStartOfHour(timestamp)                              AS hour,
    toUInt64(toFloat64OrZero(attributes['measure.frames_out']))      AS frames_out,
    toUInt64(toFloat64OrZero(attributes['measure.shadow_sent']))     AS shadow_sent,
    toUInt64(toFloat64OrZero(attributes['measure.shadow_dropped']))  AS shadow_dropped,
    toUInt64(1)                                           AS sessions
FROM agentctl.otel_spans
WHERE name = 'gateway.stream.metrics';

-- ============================================================================
-- 5. System performance table (throughput per deployment / minute) — SummingMergeTree.
-- ============================================================================
CREATE TABLE IF NOT EXISTS agentctl.throughput_1m
(
    project_id    LowCardinality(String),
    deployment_id String,
    minute        DateTime,
    bytes_total   UInt64,
    frames_total  UInt64
)
ENGINE = SummingMergeTree
PARTITION BY toDate(minute)
ORDER BY (project_id, deployment_id, minute);

CREATE MATERIALIZED VIEW IF NOT EXISTS agentctl.throughput_1m_mv
TO agentctl.throughput_1m AS
SELECT
    project_id,
    deployment_id,
    toStartOfMinute(timestamp)                       AS minute,
    toUInt64(toFloat64OrZero(attributes['measure.bytes']))      AS bytes_total,
    toUInt64(toFloat64OrZero(attributes['measure.frames_out'])) AS frames_total
FROM agentctl.otel_spans
WHERE name = 'gateway.stream.metrics';

-- ============================================================================
-- Env toggle contract (consumed by agentctl/telemetry/exporter.py + config.py):
--   TELEMETRY_BACKEND=clickhouse
--   OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317   (collector -> ClickHouse)
--   CLICKHOUSE_DSN=clickhouse://user:pass@ch-host:9000/agentctl
-- The Postgres otel_spans buffer keeps SPAN_RETENTION_DAYS_PG (default 7); ClickHouse is the
-- long-term warehouse. No schema change is needed to switch — only the exporter wiring.
-- ============================================================================
