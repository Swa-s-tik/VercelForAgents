# Design - Web dashboard (the Vercel-shaped surface)

**Status:** done · **Commit:** `feat(dashboard): web view of deployments + 1-click rollback`

## Why

agentctl had two surfaces - the CLI and Grafana - but no view of the deploy lifecycle itself.
"Vercel for agents" is, in large part, the *surface*: see your deployments, which one is live, the
canary split, and roll back with one click. Every datum for that already lives in the Postgres
system-of-record; there was just nothing reading it. This adds that view - server-rendered, zero
build step, and wired to the **real** rollback orchestrator.

## What it does

`agentctl dashboard` (-> `uvicorn agentctl.dashboard.app:app`, bound to localhost) serves:

- **Deployments** - every deployment with its weight in the *live* routing table (so you see the
  canary split at a glance), canary/shadow tags, and a **rollback-honesty** column that surfaces the
  schema-enforced truth: how many captured state mutations are side effects a rollback cannot undo.
- **1-click rollback** - a button on each eligible (sealed, not-currently-100%) deployment. It POSTs
  to `/api/rollback`, which calls `rollback_to_commit` - the *same* atomic-flip + idempotent
  state-realignment path as `agentctl rollback run` - then re-renders the page region (htmx swaps
  `#dash`). The flash honestly reports any side effects that could not be undone.
- **Rollback history** - recent rollbacks with status and an "N not undone" marker.

## Shape (deliberately dependency-light)

- `dashboard/queries.py` - thin read SQL over the controlplane SoR, returning plain dicts. Read-only;
  the only write is the rollback call in `app.py`.
- `dashboard/render.py` - **pure** functions (data in, HTML string out), so the entire UI is
  unit-tested with no server and no DB. Dark theme; htmx from a CDN drives the rollback - no SPA
  framework, no build step, no new runtime dependency (FastAPI + uvicorn were already deps).
- `dashboard/app.py` - the FastAPI app; per-request connection to the SoR.

## Boundaries (honest)

- **Local operator tool.** It reads and writes the controlplane directly and ships **no auth**, so it
  binds to `127.0.0.1` by default. Exposing it would need the API-key dependency the eval API uses;
  out of scope for this surface.
- Eval results live in DuckDB (a separate store); this view is the **deploy/routing/rollback**
  lifecycle from Postgres. Surfacing the per-deploy gate verdict here is a clean follow-up.
- Read path is per-request (no caching/streaming). Fine for an operator tool; not a public,
  high-traffic surface.

## Verified

- `tests/test_dashboard.py` - pure render units (commit/status/weight, canary+shadow tags, the
  rollback button only on eligible targets, empty states, self-contained HTML) **+** an integration
  over a seeded Postgres: the queries return the seeded live arm + the irreversible side effect, and
  the TestClient exercises `/`, `/healthz`, and a real `/api/rollback` POST (which runs the
  orchestrator and re-renders).
- Live smoke: `agentctl dashboard` serves the page (deployments, the live 100% arm, the irreversible
  honesty marker, the rollback button, history) with no server errors.

## Update: eval verdict joined in

The dashboard now also surfaces each deployment's **eval-gate verdict** (ALLOW/BLOCK + suite count +
Wilson CI), so the two halves of the product - eval and deploy - are one view. `verdicts_by_commit`
reads the DuckDB eval store (`eval_run` joined to `gate_result`), aggregates per commit (worst suite
wins), and `match_verdict` ties it to a deployment by commit SHA (exact, then prefix, to tolerate
full-vs-short shas). Read-only and best-effort: absent/locked store -> the column shows `-`. Verified
by a unit matcher test + an integration that ingests + gates into a temp DuckDB and reads the
aggregate back, plus a live smoke (a seeded deploy shows `ALLOW x3 [0.54, 0.67]`).
