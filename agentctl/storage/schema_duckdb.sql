-- DuckDB: the LOCAL OLAP store for eval traces (Vertical A).
-- Embedded file, zero external deps -> the "5-minute local setup". JSON columns are
-- stored as VARCHAR to avoid any extension dependency; ids are app-generated strings.

CREATE TABLE IF NOT EXISTS eval_run (
    run_id        VARCHAR PRIMARY KEY,
    deployment_id VARCHAR,              -- FK-by-value to Postgres deployments.id
    commit_sha    VARCHAR NOT NULL,     -- candidate commit (preview key)
    baseline_sha  VARCHAR NOT NULL,     -- usually tip of main
    pr_number     INTEGER,
    suite_name    VARCHAR NOT NULL,     -- 'correctness' | 'safety' | 'tone' | ...
    judge_name    VARCHAR,
    judge_version VARCHAR,
    started_at    TIMESTAMP,
    finished_at   TIMESTAMP,
    config_json   VARCHAR               -- frozen GateConfig used (audit)
);

-- One row per eval item, paired: candidate vs baseline on the SAME input.
CREATE TABLE IF NOT EXISTS eval_sample (
    sample_id        VARCHAR PRIMARY KEY,
    run_id           VARCHAR NOT NULL,
    item_id          VARCHAR NOT NULL,
    preference       VARCHAR NOT NULL,   -- 'WIN' | 'LOSS' | 'TIE' (candidate POV)
    cand_score       DOUBLE,
    base_score       DOUBLE,
    judge_confidence DOUBLE,
    judge_raw        VARCHAR,
    ts               TIMESTAMP
);

-- Per-arm execution trace (latency/cost/tokens). 'arm' splits candidate/baseline.
-- otel_* columns carry the production OTel->ClickHouse boundary (same span identity).
CREATE TABLE IF NOT EXISTS trace_event (
    trace_id          VARCHAR PRIMARY KEY,
    run_id            VARCHAR NOT NULL,
    sample_id         VARCHAR,
    arm               VARCHAR NOT NULL,  -- 'candidate' | 'baseline'
    span_name         VARCHAR,
    latency_ms        DOUBLE,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost_usd          DOUBLE,
    mocked            BOOLEAN,           -- did the tool-mock layer intercept this?
    otel_trace_id     VARCHAR,
    otel_span_id      VARCHAR,
    attributes_json   VARCHAR,
    ts                TIMESTAMP
);

-- Cached gate verdict per run (denormalized from gate.py for fast UI reads).
CREATE TABLE IF NOT EXISTS gate_result (
    run_id         VARCHAR PRIMARY KEY,
    decision       VARCHAR,
    n              INTEGER,
    wins           INTEGER,
    losses         INTEGER,
    ties           INTEGER,
    win_rate       DOUBLE,
    wilson_low     DOUBLE,
    wilson_high    DOUBLE,
    p_value        DOUBLE,
    bayes_p_better DOUBLE,
    margin         DOUBLE,
    computed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS eval_sample_run_idx ON eval_sample (run_id);
CREATE INDEX IF NOT EXISTS trace_event_run_idx ON trace_event (run_id, arm);
CREATE INDEX IF NOT EXISTS eval_run_pr_idx ON eval_run (pr_number);
