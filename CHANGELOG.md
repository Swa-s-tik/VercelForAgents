# Changelog

All notable changes to agentctl are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **GitHub-native eval-gate** (post-1.0). `agentctl gate --github` posts the gate verdict to a PR as a
  **commit status** (mapped from the gate's own exit code: ALLOW -> success, BLOCK -> failure, so it
  gates merge) plus a **PR comment** with the per-suite Wilson CIs - making "open a PR -> the agent is
  quality-gated automatically" a real loop instead of the webhook emulator. `--dry-run` prints both
  artifacts without calling the API; off-CI it is a safe no-op. stdlib-only poster
  (`agentctl/gitops/github_gate.py`, injectable opener for tests). Ships a reusable composite action
  (`.github/actions/agentctl-gate`) for other repos and a dogfood workflow
  (`.github/workflows/eval-gate.yml`) that gates this repo's own PRs. New: `agentctl/gitops/*`,
  `tests/test_github_gate.py`, `docs/design/GITOPS_PR_GATE.md`.
- **Header-only zero-copy forwarding on the Go data plane** (post-1.0). An opt-in
  (`AGENTCTL_ZEROCOPY=1`) fast path that proxies Frames by touching the wire bytes directly instead
  of deserializing each one: it routes by scanning `session_id` (field 1) and tags
  `attributes["canary_arm"]` (field 16) by appending a map entry to the tail - both decoder-safe and
  leaving the frozen header (fields 1-4) untouched. Benchmarked at **8.5x faster / 4.4x fewer allocs**
  on the outbound forward and **30x / 13x** on the routing read. Behavior-preserving passthrough codec
  (typed RPCs like Health are delegated to protobuf unchanged); the default data plane is byte-for-byte
  what 1.0 shipped. Proven against every golden conformance fixture and end-to-end via `agentctl push`
  + the gateway auth e2e on both paths. Retires the last "Not yet" in the README status matrix. New:
  `gateway_core/internal/wire/*`, `internal/gateway/{rawcodec,proxy_raw}.go`, generic `shadowPipe`,
  `docs/design/GO_ZEROCOPY_FORWARDING.md`.
- **PyPI packaging** (post-1.0). `pyproject.toml` now ships the runtime `.sql` schema files as
  package data (so `apply_schema` works from a `pip`-installed wheel), plus PyPI metadata
  (classifiers, keywords, project URLs). Validated locally: `python -m build` → wheel + sdist,
  `twine check` PASSED, and a clean-venv install resolves the CLI entry point, core imports, and all
  four schema files. Ready to `twine upload` when the package name is claimed.
- **Helm chart** (post-1.0). `deploy/helm/agentctl` deploys the core 3-tier (Postgres + Go gateway +
  Python control plane) to Kubernetes, mirroring the default compose topology. Ordering is enforced
  by init-containers (schema-init Job waits for Postgres; gateway/control plane wait for the schema
  table) - no hooks, no deadlock. Verified end-to-end on a kind cluster (helm lint/template, install
  --wait, pods Running + schema Job Complete, `/healthz` ok, in-cluster CLI). New:
  `deploy/helm/agentctl/*`, `docs/design/HELM_K8S.md`.
- **Users + role bindings** (post-1.0). `users` and `role_bindings` tables + `api_keys.user_id`
  extend RBAC beyond role-per-key: a key may belong to a user, and its effective role is the user's
  binding on the project (`COALESCE(binding, key.role)`), resolved identically by the Python
  resolver and the Go gateway. Standalone keys are unchanged. New `agentctl auth create-user /
  list-users` + `create-key --user`; verified on both planes (the Go gateway honors a viewer binding
  over an owner key column). New: `agentctl/auth/users.py`, schema tables, tests.
- **Control-plane + Health proto conformance** (post-1.0). Extends the golden-wire suite beyond the
  `Frame` to the `ControlPlane` service messages (RouteTable/Backend/ShadowPolicy, ResolveRoute,
  Watch, TelemetryBatch/Event/Ack) and Health - exercising nested messages, repeated fields, a
  `map<string,double>`, and enums. Cross-runtime decode interop both directions. New:
  `tests/conformance_control.py`, `gateway_core/internal/gateway/conformance_control.go`, fixtures,
  and `TestControlConformance` / `test_control_*`. The whole wire contract is now verified.
- **Full RBAC enforcement on the Go data plane** (post-1.0). The compiled Go gateway now validates
  `x-api-key` against `controlplane.api_keys` (sha256 lookup, revoked excluded) and enforces tenant
  (`project_id`) + minimum role, with a 15s TTL cache to keep Postgres off the hot path - upgrading
  the 1.0 presence-check. Degrades to presence-only without a DSN; permissive unless
  `AGENTCTL_REQUIRE_KEY=1`. New: `gateway_core/internal/gateway/auth.go` (rewritten) + `auth_test.go`
  (CI, no PG), `tests/test_go_gateway_auth.py` (real-gateway e2e, self-skips), and
  `docs/design/GO_GATEWAY_RBAC.md`.
- **Qdrant vector state store** (post-1.0): a second managed vector backend behind the `StateStore`
  protocol, selected via `AGENTCTL_STATE_BACKEND=qdrant`. Uses Qdrant's native collection aliases
  for the alias-swap rollback (historical collections preserved), reusing the exact digest contract
  so the rollback orchestrator is unchanged. Optional dep (`pip install 'agentctl[qdrant]'`) +
  optional `--profile qdrant` compose service; default backend stays `json`. New:
  `agentctl/rollback/stores/qdrant_store.py`, `tests/test_qdrant.py` (self-skips without the
  client/server), `docs/design/QDRANT_STATE_STORE.md`.
- **CI** (`.github/workflows/ci.yml`): GitHub Actions runs the two-runtime gate on every push to
  `main` and every PR - Python tests against a pgvector Postgres service, plus the Go data-plane
  build and `make conformance` (golden-wire wire-parity check).
- **`LICENSE`**: the full Apache-2.0 text (the license was already declared in `pyproject.toml`).
- CI status badge in the README.

## [1.0.0] - 2026-06-18

The production-hardening pass (`docs/ROADMAP_1_0.md`): multi-tenant RBAC, real pgvector/memory state
stores, a ClickHouse + Grafana telemetry stack, and a cross-runtime proto conformance suite. All
additions are backward-compatible - the zero-config demo and the prior test suite are unchanged.
This is the first stable release: the frozen `Frame` header, the `StateStore` protocol, and the
HTTP/gRPC auth contract are now covered by semantic versioning.

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
  from the authenticated principal instead of a hardcoded constant - backward-compatibly: a seeded
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

- **ClickHouse + Grafana telemetry** (Workstream 3). A native `ClickHouseSpanExporter` (HTTP insert,
  stdlib-only) wired into `_make_exporter` behind `TELEMETRY_BACKEND=clickhouse`, plus an optional
  `telemetry` compose profile (dockerized ClickHouse with the warehouse schema auto-applied +
  Grafana with a provisioned datasource and overview dashboard). Default stays `postgres` so the
  5-minute setup is untouched. Fixed the aggregating MVs to parse float-formatted integer measures
  (`toUInt64(toFloat64OrZero(...))`). New: `agentctl/telemetry/clickhouse_exporter.py`,
  `deploy/{clickhouse,grafana}/*`, `tests/test_clickhouse_exporter.py`,
  `docs/design/TELEMETRY_CLICKHOUSE_GRAFANA.md`.

## [0.1.0]

- Initial prototype: three verticals (probabilistic eval-gate, streaming gateway, stateful
  rollback), the Go data-plane cutover, the streaming support-agent demo, and the `agentctl push`
  developer CLI. See `README.md` and `docs/ARCHITECTURE_PRD.md`.
