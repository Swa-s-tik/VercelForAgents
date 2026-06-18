# agentctl — Road to 1.0

The three verticals, the Go data-plane cutover, the streaming demo, and the `agentctl push` CLI are
runnable and tested. 1.0 is the production-hardening pass named in the PRD (§9): the four
workstreams below, plus OSS governance and a tagged release.

Each workstream ships **runnable + tested + documented** (a design doc under `docs/design/`), as one
local commit, without breaking the existing zero-config demo or test suite.

| # | Workstream | Status | Design doc |
|---|---|---|---|
| 4 | Golden-wire proto conformance | ✅ done | [PROTO_CONFORMANCE](design/PROTO_CONFORMANCE.md) |
| 2 | API keys + multi-tenant RBAC | ✅ done | [AUTH_RBAC](design/AUTH_RBAC.md) |
| 1 | pgvector state stores | ✅ done | [PGVECTOR_STATE_STORE](design/PGVECTOR_STATE_STORE.md) |
| 3 | ClickHouse + Grafana telemetry | ✅ done | [TELEMETRY_CLICKHOUSE_GRAFANA](design/TELEMETRY_CLICKHOUSE_GRAFANA.md) |
| 5 | Governance + v1.0.0 | ⏳ planned | this file + CHANGELOG |

## The invariants that hold across the whole pass

- **Zero-config still works.** A plain `docker compose up` + `agentctl push` needs no API key, no
  pgvector, no ClickHouse. New capability is opt-in (an env flag or a compose profile).
- **Backward-compatible tenancy.** `DEMO_PROJECT_ID` keeps its literal value and is seeded as a real
  `projects` row; `resolve_principal(None)` returns it. The 7 existing test files pass unchanged.
- **Honesty preserved.** The `side_effects_are_irreversible` CHECK and the per-pointer idempotent
  rollback are untouched; new state backends reuse the exact digest contract.

## Post-1.0 (deliberately deferred)

- Qdrant / Pinecone vector adapters behind the same `StateStore` protocol.
- `users` + `role_bindings` tables (1.0 uses a lean role-per-key model).
- A hard FK `deployments.project_id → projects.id` (1.0 keeps it a soft, seeded reference).
- Full RBAC enforcement on the Go gateway (1.0 ships a wired-but-permissive metadata interceptor;
  the Python proxy enforces).
- OTLP-collector telemetry path alongside the native ClickHouse exporter.
- Control-plane proto messages in the conformance suite (1.0 covers the `Frame` hot path).
- Helm chart / k8s operator; hosted GitHub App (1.0 uses the webhook emulator).
