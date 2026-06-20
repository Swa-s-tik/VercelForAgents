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
| 5 | Governance + first tagged release (0.9.0, pre-1.0) | ✅ done | this file + CHANGELOG + CONTRIBUTING + SECURITY |

## The invariants that hold across the whole pass

- **Zero-config still works.** A plain `docker compose up` + `agentctl push` needs no API key, no
  pgvector, no ClickHouse. New capability is opt-in (an env flag or a compose profile).
- **Backward-compatible tenancy.** `DEMO_PROJECT_ID` keeps its literal value and is seeded as a real
  `projects` row; `resolve_principal(None)` returns it. The 7 existing test files pass unchanged.
- **Honesty preserved.** The `side_effects_are_irreversible` CHECK and the per-pointer idempotent
  rollback are untouched; new state backends reuse the exact digest contract.

## Post-1.0 (deliberately deferred)

- ✅ **Web dashboard** - delivered ([WEB_DASHBOARD](design/WEB_DASHBOARD.md)). `agentctl dashboard`
  serves a server-rendered view of deployments + live canary/shadow weights + rollback honesty, with a
  1-click rollback wired to the real orchestrator. The Vercel-shaped surface over the SoR.
- ✅ **GitHub-native eval-gate** - delivered ([GITOPS_PR_GATE](design/GITOPS_PR_GATE.md)).
  `agentctl gate --github` posts the verdict to a PR as a commit status (gates merge) + a comment,
  with a reusable composite action and a dogfood workflow on this repo. Makes "open a PR -> auto
  quality-gate" a real loop, retiring the webhook-emulator stand-in for the GitOps surface.
- ✅ **Qdrant** vector adapter behind the `StateStore` protocol - delivered
  ([QDRANT_STATE_STORE](design/QDRANT_STATE_STORE.md)) and **Pinecone**
  ([PINECONE_STATE_STORE](design/PINECONE_STATE_STORE.md)) - the alias-swap modelled with a pointer
  record since Pinecone has no native aliases. The named vector backends are all delivered.
- ✅ **`users` + `role_bindings`** - delivered ([AUTH_RBAC](design/AUTH_RBAC.md)). A key can belong
  to a user; its effective role is the user's project binding (`COALESCE(binding, key.role)`),
  resolved identically on both planes. Standalone keys keep the 1.0 role-per-key behavior.
- ✅ **Hard FK `deployments.project_id → projects.id`** - delivered. An ALTER after the bootstrap
  project seed; the orphan case is now rejected by the DB (`tests/test_tenancy_fk.py`).
- ✅ **Full RBAC enforcement on the Go gateway** - delivered
  ([GO_GATEWAY_RBAC](design/GO_GATEWAY_RBAC.md)). The Go gateway now validates keys against Postgres
  (sha256 lookup + tenant/role checks + a TTL cache), not just a presence check.
- ✅ **Header-only zero-copy forwarding on the Go gateway** - delivered
  ([GO_ZEROCOPY_FORWARDING](design/GO_ZEROCOPY_FORWARDING.md)). Opt-in (`AGENTCTL_ZEROCOPY=1`) fast
  path that routes by scanning `session_id` and tags `canary_arm` by appending to the wire bytes,
  with no per-frame deserialize (8.5x/30x faster on the hot ops, conformance-protected). Retires the
  last "Not yet" in the README status matrix.
- ✅ **OTLP-collector telemetry path** - delivered ([OTLP_HTTP_EXPORTER](design/OTLP_HTTP_EXPORTER.md)).
  `TELEMETRY_BACKEND=otlp` ships OTLP-JSON over HTTP to any collector, stdlib-only (no extra dep).
- ✅ **Control-plane + Health proto messages in the conformance suite** - delivered
  ([PROTO_CONFORMANCE](design/PROTO_CONFORMANCE.md)). The whole wire contract (Frame + control plane
  + Health) is now cross-runtime decode-verified.
- ✅ **Helm chart** - delivered ([HELM_K8S](design/HELM_K8S.md)); deploys the core 3-tier to
  Kubernetes, verified end-to-end on a kind cluster.
- ✅ **Declarative API: AgentDeployment CRD + reconcile** - delivered
  ([CRD_OPERATOR](design/CRD_OPERATOR.md)). The custom resource + `agentctl apply -f` (one-shot
  reconcile via the gated-rollout orchestrator) ship now; the watch-loop controller is shipped too
  (`agentctl operator run`, a kopf wrapper around the reconcile; Helm-deployable, off by default); the hosted
  GitHub App's brain ships too - a signed webhook receiver that gates PRs
  ([GITHUB_APP](design/GITHUB_APP.md), `agentctl gitops-app`); only the hosting (public URL + App
  registration + per-install tokens) remains, which is ops, not control-plane code.
