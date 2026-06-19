# Design - Full RBAC on the Go data plane (post-1.0)

**Status:** done · **Commit:** `feat(auth): full RBAC on the Go gateway …`

## Why

1.0 enforced API keys at the FastAPI surfaces and the Python gRPC proxy, but the **compiled Go
gateway** - the default data plane - only *presence-checked* `x-api-key` (the control plane / SoR
was the validity authority). `docs/design/AUTH_RBAC.md` flagged full Go-side validation as a
deliberate post-1.0 item. This closes it: the Go gateway now validates keys against Postgres and
enforces tenant + role, so the data plane is a real enforcement point.

## What it does

`gateway_core/internal/gateway/auth.go::Authenticator`, wired as the gRPC stream + unary
interceptors in `cmd/gateway/main.go`:

1. **Validate** - sha256-hash the `x-api-key` (same `hashKey` as Python; unit-asserted against the
   seeded bootstrap hash) and look it up in `controlplane.api_keys` (`WHERE key_hash=$1 AND
   revoked_at IS NULL`). Unknown/revoked → `UNAUTHENTICATED`.
2. **Tenant isolation** - the key's `project_id` must equal the gateway's `AGENTCTL_PROJECT_ID`
   (the tenant it serves), else `PERMISSION_DENIED`.
3. **Role floor** - `roleRank[key.role] >= roleRank[AGENTCTL_MIN_ROLE]` (default `viewer`), else
   `PERMISSION_DENIED`.
4. **Hot-path cache** - results are cached for a 15s TTL (RWMutex-guarded map) so Postgres isn't hit
   per stream/connection; revocations take effect within the TTL.

### Degradation & failure modes (deliberate)
- **No DSN** (`AGENTCTL_PG_DSN` unset → static routing) → presence-check only, exactly the prior
  behavior. So offline/unit use is unchanged.
- **Permissive by default** - a missing key is allowed unless `AGENTCTL_REQUIRE_KEY=1`. The demo and
  conformance flows run keyless.
- **Fail-open on a DB blip** - a transient Postgres error during lookup allows the call (availability
  over strictness), mirroring the Python interceptor. Explicit, not accidental.

## Verification

- **Go unit test** (`auth_test.go`, runs in CI without Postgres): `hashKey` matches Python's
  hexdigest, and the pure `decide(...)` function returns the right code across no-key/required,
  presence-only, not-found, valid, wrong-project, and insufficient-role cases.
- **Python e2e** (`tests/test_go_gateway_auth.py`, local - skips without the built binary + PG):
  launches the **real** Go gateway with `AGENTCTL_REQUIRE_KEY=1` and drives its Health RPC -
  bootstrap key → `ready`; no key / garbage key → `UNAUTHENTICATED`; a valid key from a *different*
  project → `PERMISSION_DENIED`.

```bash
cd agentctl/gateway_core && make build && go test ./internal/gateway/
python -m pytest tests/test_go_gateway_auth.py -q
```

## Boundaries / post-1.0

- The cache is per-gateway-process and TTL-based; a revocation propagates within `ttl` (15s). A
  push-based invalidation (LISTEN/NOTIFY on key changes, like routing) is a future refinement.
- Per-RPC role policy is uniform (one `AGENTCTL_MIN_ROLE` for the data plane); finer per-method
  policy can layer on later.
