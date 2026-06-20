"""Environment-driven settings (no pydantic dependency needed for the prototype)."""
from __future__ import annotations

import os

# Postgres system-of-record. docker-compose maps host 5433 -> container 5432.
PG_DSN = os.environ.get(
    "AGENTCTL_PG_DSN", "postgresql://agentctl:agentctl@localhost:5433/agentctl")

# DuckDB local OLAP store (Vertical A).
DUCKDB_PATH = os.environ.get("AGENTCTL_DUCKDB", ".agentctl/eval.duckdb")

# External-store STUB persistence (Vertical C demo) - lets `seed` and `rollback` run as
# separate CLI invocations and still share the simulated vector/memory/schema state.
STATE_FILE = os.environ.get("AGENTCTL_STATE_FILE", ".agentctl/state/external_state.json")

# State backend for the vector/memory stores: 'json' (default, file-backed stubs - zero infra) or
# 'pgvector' (real pgvector + Postgres event-sourced memory; needs the pgvector image + schema).
STATE_BACKEND = os.environ.get("AGENTCTL_STATE_BACKEND", "json")  # json | pgvector | qdrant | pinecone
VECTOR_DIM = int(os.environ.get("AGENTCTL_VECTOR_DIM", "8"))
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX = os.environ.get("PINECONE_INDEX", "agentctl")
# GitHub App webhook (agentctl gitops-app): the App's webhook secret + token for posting back.
GH_WEBHOOK_SECRET = os.environ.get("AGENTCTL_GH_WEBHOOK_SECRET", "")
GH_TOKEN = os.environ.get("AGENTCTL_GH_TOKEN", "")

# Telemetry boundary: 'postgres' (default, short buffer) | 'clickhouse' (prod warehouse).
TELEMETRY_BACKEND = os.environ.get("TELEMETRY_BACKEND", "postgres")
CLICKHOUSE_DSN = os.environ.get("CLICKHOUSE_DSN", "")
CLICKHOUSE_HTTP_ENDPOINT = os.environ.get("CLICKHOUSE_HTTP_ENDPOINT", "http://localhost:8123")
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "agentctl")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "agentctl")
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
# OTLP/HTTP collector endpoint for TELEMETRY_BACKEND=otlp (stdlib exporter; no extra dep).
OTLP_HTTP_ENDPOINT = os.environ.get(
    "AGENTCTL_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")

# A fixed project id so the prototype demo is reproducible.
DEMO_PROJECT_ID = os.environ.get("AGENTCTL_PROJECT_ID", "00000000-0000-0000-0000-0000000000a1")
