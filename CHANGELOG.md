# Changelog

All notable changes to agentctl are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Road to 1.0

The production-hardening pass (`docs/ROADMAP_1_0.md`). All additions are backward-compatible: the
zero-config demo and the existing test suite are unchanged.

### Added
- **Golden-wire proto conformance suite** (Workstream 4). Cross-runtime verification that the Python
  reference proxy and the Go data plane are wire-compatible on the frozen `Frame` envelope:
  byte-identical frozen header (fields 1–4) + lossless cross-runtime decode in both directions.
  Surfaced and documented that protobuf `deterministic` marshaling is per-runtime, not cross-runtime
  canonical. New: `tests/fixtures/conformance_frames.json`, `tests/conformance_frames.py`,
  `tests/test_conformance.py`, `gateway_core/internal/gateway/conformance{,_test}.go`,
  `gateway_core/cmd/genfixtures`, `make fixtures` / `make conformance`, and
  `docs/design/PROTO_CONFORMANCE.md`. The first Go test in the repo.

- **Multi-tenant RBAC via API keys** (Workstream 2). `orgs`/`projects`/`api_keys` tables
  (role-per-key: viewer/developer/admin/owner; sha256-hashed secrets). `project_id` is now resolved
  from the authenticated principal instead of a hardcoded constant — backward-compatibly: a seeded
  bootstrap project/key means `resolve_principal(None)` returns the demo project, so zero-config
  `agentctl push` and all existing tests are unchanged. Enforcement at FastAPI (`Depends`), a gRPC
  interceptor (Python proxy) + wired presence-check (Go gateway, `AGENTCTL_REQUIRE_KEY=1`), and the
  CLI (`--api-key` + `agentctl auth create-key/list-keys/revoke-key`). New: `agentctl/auth/*`,
  `tests/test_auth.py`, `docs/design/AUTH_RBAC.md`.

- **Real pgvector state stores** (Workstream 1). The vector and memory `StateStore` stubs now have
  production adapters: `PgVectorStore` (pgvector collections + an idempotent alias-swap restore) and
  `PgMemoryStore` (Postgres event-sourced log + HEAD rewind), reusing the exact digest contract so
  Vertical C's rollback orchestrator + Phase-3 verification are unchanged. Env-gated
  (`AGENTCTL_STATE_BACKEND=pgvector`); default stays the file-backed stubs so offline tests need no
  infra. Compose image is now `pgvector/pgvector:pg16` (a strict superset). New:
  `agentctl/rollback/stores/{schema_vector.sql,vector_pg.py,memory_pg.py}`, `tests/test_pgvector.py`,
  `docs/design/PGVECTOR_STATE_STORE.md`.

<!-- subsequent workstreams appended here as they land: ClickHouse/Grafana -->

## [0.1.0]

- Initial prototype: three verticals (probabilistic eval-gate, streaming gateway, stateful
  rollback), the Go data-plane cutover, the streaming support-agent demo, and the `agentctl push`
  developer CLI. See `README.md` and `docs/ARCHITECTURE_PRD.md`.
