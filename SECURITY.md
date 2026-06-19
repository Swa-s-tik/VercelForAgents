# Security Policy

## Reporting a vulnerability

Email **proefficosolutions@gmail.com** with details and a reproduction. Please do not open a public
issue for an undisclosed vulnerability. We aim to acknowledge within a few business days.

## The security model (1.0)

agentctl is a control plane; its trust boundaries are deliberate and documented.

### Authentication & authorization
- **API keys, hashed at rest.** Only `sha256(secret)` is stored in `controlplane.api_keys`; the
  secret is shown once at creation. A 12-char prefix is the only safe-to-log identifier.
- **Role-per-key RBAC** (`viewer < developer < admin < owner`) enforced at the FastAPI surfaces
  (`Depends`), the Python gRPC proxy (interceptor), and the CLI. See `docs/design/AUTH_RBAC.md`.
- **Key rotation/revocation:** `agentctl auth create-key` / `revoke-key`. Revoked keys
  (`revoked_at` set) are rejected immediately at resolution.
- **Enforcement is opt-in by default** so the zero-config demo works; set `AGENTCTL_REQUIRE_KEY=1` to
  make a valid key mandatory on the HTTP and gRPC surfaces.
- **Webhook authenticity:** `POST /webhook/git` verifies an HMAC (`AGENTCTL_WEBHOOK_SECRET`) over the
  payload; the API key independently selects the tenant.

### Known boundaries (by design in 1.0; tracked in `docs/ROADMAP_1_0.md`)
- The **Go gateway** presence-checks `x-api-key` only when `AGENTCTL_REQUIRE_KEY=1`; full key
  validation against Postgres on the Go side is post-1.0 (the control plane / SoR is the authority).
- `deployments.project_id` is a **soft** reference to `projects` in 1.0 (no hard FK).
- The bootstrap key is a **well-known development credential** (`actl_dev_bootstrap_…`, owner role on
  the demo project). It exists to make the local demo zero-config. **Rotate or revoke it before any
  shared/production deployment.**
- TLS is not terminated by agentctl itself; run it behind a TLS-terminating proxy.

### Data-integrity guarantees
- **Honesty is schema-enforced:** `CHECK (mutation_class <> 'side_effect' OR reversibility =
  'irreversible')` - the system cannot record an external side effect (a charge, an email) as
  reversible. Rollbacks that touch irreversible state report `compensating`, never a fake
  `completed`.
- The live routing flip is a single advisory-locked ACID transaction guarded by the
  `one_live_routing_per_project` partial-unique index - the gateway never reads a torn routing table.

## Secrets hygiene
- Never commit real API keys, `AGENTCTL_WEBHOOK_SECRET`, or database credentials. The compose files
  use throwaway local credentials (`agentctl/agentctl`) suitable only for local development.

## Supported versions
1.0.x receives security fixes. Pre-1.0 prototypes are not supported.
