# agentctl - Road to 1.0

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
| 5 | Governance + v1.0.0 | ✅ done | this file + CHANGELOG + CONTRIBUTING + SECURITY |

## The invariants that hold across the whole pass

- **Zero-config still works.** A plain `docker compose up` + `agentctl push` needs no API key, no
  pgvector, no ClickHouse. New capability is opt-in (an env flag or a compose profile).
- **Backward-compatible tenancy.** `DEMO_PROJECT_ID` keeps its literal value and is seeded as a real
  `projects` row; `resolve_principal(None)` returns it. The 7 existing test files pass unchanged.
- **Honesty preserved.** The `side_effects_are_irreversible` CHECK and the per-pointer idempotent
  rollback are untouched; new state backends reuse the exact digest contract.

## Post-1.0 (deliberately deferred)

- ✅ **Qdrant** vector adapter behind the `StateStore` protocol - delivered
  ([QDRANT_STATE_STORE](design/QDRANT_STATE_STORE.md)). A Pinecone adapter is the remaining one.
- ✅ **`users` + `role_bindings`** - delivered ([AUTH_RBAC](design/AUTH_RBAC.md)). A key can belong
  to a user; its effective role is the user's project binding (`COALESCE(binding, key.role)`),
  resolved identically on both planes. Standalone keys keep the 1.0 role-per-key behavior.
- A hard FK `deployments.project_id → projects.id` (1.0 keeps it a soft, seeded reference).
- ✅ **Full RBAC enforcement on the Go gateway** - delivered
  ([GO_GATEWAY_RBAC](design/GO_GATEWAY_RBAC.md)). The Go gateway now validates keys against Postgres
  (sha256 lookup + tenant/role checks + a TTL cache), not just a presence check.
- ✅ **Header-only zero-copy forwarding on the Go gateway** - delivered
  ([GO_ZEROCOPY_FORWARDING](design/GO_ZEROCOPY_FORWARDING.md)). Opt-in (`AGENTCTL_ZEROCOPY=1`) fast
  path that routes by scanning `session_id` and tags `canary_arm` by appending to the wire bytes,
  with no per-frame deserialize (8.5x/30x faster on the hot ops, conformance-protected). Retires the
  last "Not yet" in the README status matrix.
- OTLP-collector telemetry path alongside the native ClickHouse exporter.
- ✅ **Control-plane + Health proto messages in the conformance suite** - delivered
  ([PROTO_CONFORMANCE](design/PROTO_CONFORMANCE.md)). The whole wire contract (Frame + control plane
  + Health) is now cross-runtime decode-verified.
- ✅ **Helm chart** - delivered ([HELM_K8S](design/HELM_K8S.md)); deploys the core 3-tier to
  Kubernetes, verified end-to-end on a kind cluster. A full CRD operator + hosted GitHub App remain.
