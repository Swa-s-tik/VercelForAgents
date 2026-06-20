# Changelog

All notable changes to agentctl are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Honest re-versioning to pre-1.0 (0.9.0).** The earlier `1.0.0` tag overstated maturity: the
  project ships several deliberate stubs, the data-plane/control-plane integration tests are CI-only
  (they need Postgres/gRPC), and there are no external production users. The package is now `0.9.0`
  to signal "feature-complete on the roadmap, but pre-stable - the public API may still move." The
  wire `Frame` header, the `StateStore` protocol, and the auth contract are still the surfaces we aim
  to keep stable; SemVer guarantees resume at a real `1.0.0`.

### Fixed
- **Correctness + safety pass** (post-roadmap review):
  - *Rollback honesty*: a canary leaves 2+ active deployments, but the rollback inspected only one
    arbitrary demoted manifest, so an irreversible side-effect on another demoted arm could be dropped
    and the rollback faked as `completed`. Every demoted arm is now scanned, and the full demoted set
    is persisted so crash-resume re-runs the union.
  - *Dashboard authz/CSRF*: the destructive `POST /api/rollback`, `/api/rollout` and the full-snapshot
    `GET /api/state` had no auth; they now require `principal_dep` (admin / developer / viewer).
  - *Rollout*: `set_canary` collapsed a multi-primary table to the heaviest arm (dropping the others'
    traffic); it now rescales every primary proportionally, plus weight-rounding guards and a closed
    DuckDB handle leak.
  - *Gateway*: on client disconnect/primary error the shadow pump hung and leaked the shadow tasks +
    gRPC calls; teardown now cancels and closes them (and closes shadows concurrently, not N x 2s).
  - *GitHub App webhook*: the blocking gate + GitHub POSTs ran on the asyncio event loop; moved to a
    worker thread so concurrent deliveries are not starved.
  - *Operator*: malformed CRs raised `ValueError`, which kopf retried with backoff forever; now mapped
    to `kopf.PermanentError` (status `phase=Invalid`).
  - *Route cache*: the LISTEN/NOTIFY watcher died silently on a dropped connection, freezing the
    gateway on a stale routing table; both watchers now reconnect with capped backoff and reload on
    reconnect.
- **Helm chart nits**: `postgres.storage` was set but unused (the volume was always `emptyDir`) - now
  `postgres.persistence.{enabled,size}` selects a real PVC vs emptyDir (default emptyDir for kind).
  And `helm upgrade` is idempotent: the destructive schema-init Job renders only on install (gated by
  `schemaInit.runOnUpgrade`, off by default), so an upgrade no longer hits the immutable-Job error or
  re-wipes the control plane. Verified via `helm lint` + `helm template` across install/upgrade and
  persistence on/off.

### Added
- **GitHub App webhook receiver** (post-1.0). `agentctl gitops-app` runs the hosted App's brain: a
  signed (`X-Hub-Signature-256` HMAC, fail-closed) webhook receiver that routes `pull_request` events,
  gates the PR against its ingested eval runs, and posts the verdict back via `github_gate`. The
  webhook-triggered counterpart of the CI-triggered reusable Action; hosting (a public URL + the App
  registration + per-install tokens) is the remaining ops step. New: `agentctl/gitops/webhook_app.py`,
  `tests/test_github_app.py`, `docs/design/GITHUB_APP.md`.
- **Shadow output divergence** (post-1.0). The shadow lane mirrors traffic to a candidate backend but
  used to discard its responses with no signal. The proxy now emits `shadow_received` (frames the
  shadow produced) per stream, and the dashboard's Live-traffic panel shows shadow-vs-primary output
  divergence per arm (`N frames (X% diverge)`, flagged at >=20%) - so a shadow actually tells you how
  a candidate *would have* responded, not just that it didn't crash. New: `_record_metrics` shared by
  both proxy paths; `queries.stream_telemetry` + `render` updated.
- **AgentDeployment watch-loop controller** (post-1.0). `agentctl operator run` (a kopf wrapper, opt
  dep `agentctl[operator]`) watches AgentDeployment CRs and reconciles each via the tested
  `reconcile_body` -> writes `.status`. The Helm chart deploys it (off by default, `operator.enabled`)
  with a ServiceAccount + ClusterRole/Binding. So `kubectl apply` of an AgentDeployment now drives a
  real rollout. New: `agentctl/operator/controller.py`, `deploy/helm/agentctl/templates/operator.yaml`.
- **Declarative API: the AgentDeployment CRD + `agentctl apply`** (post-1.0). A custom resource
  (`agentctl.dev/v1alpha1`, kind `AgentDeployment`: `commit`, `weight`, `requireGatePR`, `nim`) and a
  reconcile that drives live routing to match it - `gated_rollout` when a gate PR is required, else
  `set_canary`. `agentctl apply -f <cr>.yaml` runs the reconcile one-shot, so the declarative API works
  today with no controller. A watch-loop controller and a hosted GitHub App are thin wrappers around
  this reconcile (documented). New: `agentctl/operator/*`, `deploy/crds/*`, `tests/test_operator.py`,
  `docs/design/CRD_OPERATOR.md`.
- **Header-only fast path on the Python reference proxy** (post-1.0). The Python proxy now has the
  same opt-in (`AGENTCTL_ZEROCOPY=1`) header-only forwarding as the Go data plane: `gateway/wire.py`
  scans `session_id` and appends `canary_arm` on the wire bytes (no per-frame deserialize), pinned
  byte-for-byte against the same golden conformance fixtures (20 cases). `serve()` registers a raw
  `Converse` (typed `Health`) when zero-copy is on; default registration is unchanged. New:
  `agentctl/gateway/wire.py`, `tests/test_wire_py.py`, `tests/test_proxy_zerocopy.py`.
- **Pinecone vector StateStore** (post-1.0): the third managed vector backend, selected via
  `AGENTCTL_STATE_BACKEND=pinecone`. Pinecone has no native aliases, so the alias-swap rollback is
  modelled with a pointer record (one `id=pointer` in a `__live__<project>` namespace whose metadata
  names the live namespace); historical namespaces are preserved, reusing the exact digest contract.
  Optional dep (`pip install 'agentctl[pinecone]'`). The constructor takes an injected index, so the
  StateStore semantics are unit-tested against an in-memory fake (no account); a live test self-skips.
  New: `agentctl/rollback/stores/pinecone_store.py`, `tests/test_pinecone.py`,
  `docs/design/PINECONE_STATE_STORE.md`.
- **Community scaffolding** (post-1.0). `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1),
  `GOVERNANCE.md` (roles, decision process, release policy), and GitHub issue/PR templates
  (`.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`).

### Fixed
- **Punctuation hygiene**: purged all non-ASCII dashes (em-dash, en-dash, horizontal bar) project-wide
  to plain `-`, including the dashes that crept back into the Go data-plane files.
- **Landing page**: the status matrix listed header-only zero-copy forwarding as PLANNED; it shipped
  (Go, opt-in) in the zero-copy PR. `index.html` now marks it REAL with the Python-proxy caveat.
- **Dashboard auto-refresh** (post-1.0). The dashboard now polls a fragment endpoint
  (`GET /api/dashboard`) via htmx every few seconds, so the live-traffic, eval-verdict, and routing
  panels update without a manual reload - the "Live traffic" panel is actually live.
- **Machine-readable control-plane state** (post-1.0). A JSON view of the whole control plane -
  deployments, eval verdicts, live traffic, routing timeline, rollbacks - served at `GET /api/state`
  (dashboard) and emitted by `agentctl status --json`, so CI/external tools can consume it. Shared
  `queries.json_snapshot` normalizes datetimes/Decimals to JSON-safe types. New tests cover the
  endpoint + serialization.
- **Batched telemetry export** (post-1.0). `make_tracer_provider(batch=True)` (or
  `AGENTCTL_TELEMETRY_BATCH=1`) wraps any span exporter in a `BatchSpanProcessor`, so a busy gateway
  queues spans and flushes them off the hot path instead of paying an export round-trip per span.
  Default stays the synchronous `SimpleSpanProcessor` (spans visible immediately for the demo). Works
  with every backend (postgres/clickhouse/otlp). New: `tests/test_telemetry_batch.py`.
- **Gated rollout** (post-1.0). `agentctl rollback rollout <commit> --require-gate <PR>` runs that
  PR's eval gate and rolls out only if it ALLOWs - the safety interlock tying eval to delivery, so a
  regression can't be promoted by mistake (BLOCK/INCONCLUSIVE -> no routing change). New:
  `rollout.gated_rollout` + tests.
- **Delivery timeline** (post-1.0). A unified routing-change history (from `routing_tables`): every
  rollback, canary, and promote shown with its reason, the per-arm weight split, and who/when - in
  both the dashboard and `agentctl status`. The rollbacks-only history couldn't show forward changes;
  this does. New: `queries.routing_history`, `render.routing_history_table`.
- **Forward rollout in the dashboard** (post-1.0). Each eligible deployment now has **canary 10%** and
  **promote** buttons (next to rollback) that POST to `/api/rollout/{sha}/{pct}` -> `set_canary`, so the
  web UI drives traffic forward and back - a symmetric control surface. New dashboard tests cover the
  buttons + a canary-then-promote POST flow.
- **Progressive rollout** (post-1.0). `agentctl rollback rollout <commit> --weight <pct>` rolls
  *forward* by percentage - the complement of rollback. A canary (`--weight` < 100) splits live
  traffic between the target and the current primary (shadows preserved); `--weight 100` is a full
  promote. Both reuse the same atomic, advisory-locked routing flip + `pg_notify` as rollback, so the
  live gateway re-routes instantly and correctness is inherited. New: `agentctl/rollback/rollout.py`,
  `tests/test_rollout.py`, `docs/design/PROGRESSIVE_ROLLOUT.md`.
- **GitHub Check Run for the eval-gate** (post-1.0). `agentctl gate --check-run` posts a richer Check
  Run alongside the commit status: a markdown summary (the per-suite Wilson-CI table) in the Checks
  tab and a **`neutral`** conclusion for an INCONCLUSIVE gate that isn't a hard block - which a commit
  status can't express. Composes with `--github`; needs `checks: write`. The dogfood workflow + the
  reusable action now pass `--check-run`. New: `check_run_payload`/`post_check_run` + tests.
- **`agentctl status`** (post-1.0). The terminal counterpart of the web dashboard: a rich summary of
  deployments (status, eval verdict, live traffic weight, rollback honesty), recent gateway traffic by
  canary arm, and recent rollbacks - reading the same system-of-record + DuckDB verdicts via the shared
  `dashboard.queries`, so the CLI and web surfaces never drift. New: `agentctl/cli/status.py`,
  `tests/test_cli_status.py`.
- **Live traffic panel in the dashboard** (post-1.0). The dashboard now surfaces the data plane's
  real telemetry: recent gateway streams aggregated by canary arm (streams, frames forwarded, shadow
  drops, average latency), read from the same `otel_spans` the telemetry exporter writes. With the
  eval verdict and rollback honesty already in view, the dashboard is now one surface for the whole
  lifecycle - deploy, eval, traffic, rollback. New: `queries.stream_telemetry`, `render.traffic_table`.
- **Eval verdict in the dashboard** (post-1.0). The dashboard now joins the *eval* surface to the
  *deploy* surface: each deployment shows its eval-gate verdict (ALLOW/BLOCK + suite count + Wilson
  CI), read from the DuckDB eval store and matched to the deployment by commit SHA (exact or prefix).
  Read-only, degrades to '-' when there is no eval store - one integrated control-plane view of the
  whole lifecycle. New: `queries.verdicts_by_commit`, `render.match_verdict`, dashboard tests.
- **Hard tenancy FK** (post-1.0). `deployments.project_id` now has a real foreign key to
  `projects(id)` (RESTRICT) - 1.0 deliberately kept it a soft, seeded reference. Added by ALTER after
  the bootstrap project (the historic DEMO_PROJECT_ID) is seeded, so every deployment resolves to a
  real project and the orphan case is now rejected by the database. New: `tests/test_tenancy_fk.py`.
- **OTLP/HTTP telemetry exporter** (post-1.0). `TELEMETRY_BACKEND=otlp` now ships OTel spans as
  OTLP-JSON over HTTP to any collector's `/v1/traces` (otelcol, Tempo, Jaeger, Honeycomb, Grafana
  Cloud) with **no new dependency** - stdlib urllib, same discipline as the ClickHouse exporter
  (never crashes the caller). Replaces the old `otlp` path that needed the heavy gRPC OTLP package
  (kept behind `otlp-grpc`). Pure OTLP payload builder is unit-tested against real tracer spans. New:
  `agentctl/telemetry/otlp_exporter.py`, `tests/test_otlp_exporter.py`, `docs/design/OTLP_HTTP_EXPORTER.md`.
- **Web dashboard** (post-1.0). `agentctl dashboard` serves a server-rendered view of the deploy
  lifecycle from the Postgres system-of-record: deployments with their live canary/shadow routing
  weights, a rollback-honesty column (schema-enforced irreversible side effects), rollback history,
  and a **1-click rollback** button that calls the real `rollback_to_commit` orchestrator (htmx swaps
  the page region). Zero build step, no new dependency (FastAPI + uvicorn were already deps); pure
  render functions are unit-tested and a TestClient exercises the page + a real rollback POST over a
  seeded Postgres. Local operator tool (binds to localhost, no auth). New: `agentctl/dashboard/*`,
  `tests/test_dashboard.py`, `docs/design/WEB_DASHBOARD.md`.
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

## [0.9.0] - 2026-06-18

> Originally tagged `1.0.0`; re-versioned to `0.9.0` (see the Unreleased "Honest re-versioning"
> note) because the project is feature-complete on the roadmap but still pre-stable.

The production-hardening pass (`docs/ROADMAP_1_0.md`): multi-tenant RBAC, real pgvector/memory state
stores, a ClickHouse + Grafana telemetry stack, and a cross-runtime proto conformance suite. All
additions are backward-compatible - the zero-config demo and the prior test suite are unchanged.
This is the roadmap-complete milestone: the frozen `Frame` header, the `StateStore` protocol, and the
HTTP/gRPC auth contract are the surfaces we aim to keep stable, with full SemVer guarantees resuming
at a real `1.0.0`.

### Added
- **Golden-wire proto conformance suite** (Workstream 4). Cross-runtime verification that the Python
  reference proxy and the Go data plane are wire-compatible on the frozen `Frame` envelope:
  byte-identical frozen header (fields 1-4) + lossless cross-runtime decode in both directions.
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
