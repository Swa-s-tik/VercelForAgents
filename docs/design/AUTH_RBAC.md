# Design — API keys + multi-tenant RBAC (Workstream 2)

**Status:** done · **Commit:** `feat(auth): …`

## Why

Every control-plane table was already `project_id`-scoped, but the project was the hardcoded
`DEMO_PROJECT_ID` and there was **no authentication** anywhere: the webhook had only HMAC, the eval
API was open, and the gRPC gateway used `add_insecure_port`. 1.0 needs a real identity +
authorization model — without breaking the zero-config demo or the existing tests.

## Model

API keys with a **role per key** (no external IdP). Three tables appended to
`schema_postgres.sql` (`controlplane`):

- `orgs(id, slug)`
- `projects(id, org_id, slug)` — `id` is the existing tenancy dimension
- `api_keys(id, project_id, name, key_prefix, key_hash, role, revoked_at)` — only the **sha256 hash**
  of the secret is stored; `key_prefix` (first 12 chars) is safe to display; `role ∈
  {viewer, developer, admin, owner}`.

`users` / `role_bindings` are intentionally **not** in 1.0 — role-per-key is the right altitude for a
key-authenticated control plane and avoids join overhead on the auth hot path. They're a documented
post-1.0 extension.

### Permission matrix

| Action | Surface | Min role |
|---|---|---|
| read (gate, routing, audit) | eval API GET, `rollback routing/audit` | `viewer` |
| push / deploy / routing flip | `agentctl push`, `POST /webhook/git` | `developer` |
| rollback run | `rollback run` | `admin` |
| schema / seed / key management | `rollback schema|seed`, `auth create/revoke` | `owner` |

`ROLE_RANK = {viewer:0, developer:1, admin:2, owner:3}`; `Principal.require(min)` enforces
`rank >= ROLE_RANK[min]`.

## The backward-compat keystone (the load-bearing decision)

Making `project_id` dynamic could have broken every call site and all 7 tests. It doesn't, because:

1. `DEMO_PROJECT_ID`'s literal value is **unchanged** and is now **seeded as a real `projects` row**
   (with a `default` org and a bootstrap **owner** key) by an idempotent `INSERT … ON CONFLICT DO
   NOTHING` at the end of `schema_postgres.sql`. Every `apply_schema` re-seeds it, so tests that
   `apply_schema` in `setup_module` get the bootstrap rows for free.
2. `resolve_principal(conn, api_key)` is the single chokepoint. **`api_key=None` returns the
   bootstrap owner Principal scoped to `DEMO_PROJECT_ID` with no DB access.** Every surface swapped
   `DEMO_PROJECT_ID` for `principal.project_id`, whose default value is identical.
3. **No hard FK** `deployments.project_id → projects.id` (would force ordering changes in
   seed/tests). `projects` is a soft, seeded reference — a documented v1.1 hardening item.

Result: `agentctl push` with no key still works (`principal bootstrap · role=owner · project=…a1`),
and all existing tests pass unchanged.

## Enforcement points

- **HTTP (`agentctl/auth/fastapi_dep.py`):** `principal_dep(min_role)` reads `X-API-Key` /
  `Authorization: Bearer`. No key + not required → bootstrap (no DB hit); `AGENTCTL_REQUIRE_KEY=1`
  makes a key mandatory (401). Applied to the eval API (`viewer`).
- **Webhook (`control/webhook.py`):** HMAC still authenticates the **payload**; the API key selects
  the **tenant**. Precedence: `X-API-Key` → bootstrap project. Both independently optional, so
  `test_webhook` (no key, no secret) passes.
- **gRPC — Python proxy (`auth/grpc_interceptor.py`):** `ApiKeyServerInterceptor` validates a
  present key against Postgres and aborts `UNAUTHENTICATED` (same-cardinality handler) on
  invalid/missing-when-required. Permissive by default → existing gateway tests are keyless and
  unchanged.
- **gRPC — Go gateway (`gateway_core/internal/gateway/auth.go`):** in 1.0, presence-checked
  `x-api-key` only. **Post-1.0 this was upgraded to full validation** — sha256 lookup against
  `controlplane.api_keys` + tenant (`project_id`) and role checks, with a TTL cache. See
  `docs/design/GO_GATEWAY_RBAC.md`.
- **CLI:** `--api-key` / `AGENTCTL_API_KEY` on `push` and the `rollback` subcommands; new
  `agentctl auth {create-key,list-keys,revoke-key}`.

## Verification

```bash
python -m pytest tests/test_auth.py -q          # key primitives, role ranking, DB resolution, revocation
python -m pytest -q                             # all 7 existing files still green (keystone)
agentctl auth list-keys                         # shows the bootstrap owner key
agentctl push                                   # zero-config: principal=bootstrap → MERGED
AGENTCTL_REQUIRE_KEY=1 agentctl push            # now requires a valid key
```

## Boundaries / post-1.0

- `users` + `role_bindings` (1.0 = role-per-key).
- Hard FK `deployments.project_id → projects.id`.
- Full key validation on the Go gateway (1.0 presence-checks only).
- Per-project key scoping for cross-project admin (1.0 keys are single-project).
