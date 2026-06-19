# Design - Progressive rollout (forward canary / promote)

**Status:** done · **Commit:** `feat(rollout): progressive canary % + full promote`

## Why

agentctl could roll *back* (1-click flip to a prior deployment) but not roll *forward* by percentage -
the other half of the "boring, instant, reversible" delivery story. `agentctl rollback rollout` adds
progressive delivery: shift live traffic to a deployment a slice at a time (canary) or all at once
(promote).

## What it does

`agentctl rollback rollout <commit> --weight <pct>` (developer role):

- **Canary (`--weight` < 100)**: installs a live routing table that sends `pct%` to the target
  (flagged `is_canary`) and the remainder to the current primary, **preserving any shadow targets**.
  The target deployment's status flips to `active` (it is now a real serving arm).
- **Promote (`--weight 100`)**: a full 100% cutover via `flip_routing` (which also flips deployment
  statuses: target -> active, the previous live -> rolled_back).

Both go through the **same atomic, advisory-locked routing flip** that rollback uses
(`install_weighted` / `flip_routing` in `rollback/routing.py`): one transaction, the
`one_live_routing_per_project` partial-unique index holds at every statement, and a `pg_notify`
fires so the live Go gateway re-routes instantly. So forward rollout inherits rollback's correctness
for free - it is the same primitive, driven by a weight instead of a revert.

## Boundaries (honest)

- It sets *weights*; it does not run the eval-gate first (gate then rollout is a two-step the operator
  composes - `agentctl gate` then `agentctl rollback rollout`). Auto-promote-on-pass is a clean
  follow-up.
- Canary picks the single highest-weight non-shadow arm as "the primary" to split against. Multi-arm
  splits beyond primary+canary aren't modeled (the common case is one-stable + one-canary).

## Verified

- `tests/test_rollout.py`: a 20% canary (A 2000 bps `is_canary` + B 8000 bps, weights total 10000,
  A flipped to `active`), then a promote to 100% (A 10000 bps, B dropped + `rolled_back`); plus
  validation (weight out of (0,100], unknown commit). Full suite 159 passed.
- Live: `rollback rollout aaaa... --weight 25` -> B 75% / A 25% canary (routing v2); `--weight 100`
  -> A 100% (routing v3).

## Update: gated rollout (eval interlock)

`agentctl rollback rollout <commit> --weight W --require-gate <PR>` runs that PR's eval gate first and
rolls out **only if it ALLOWs** - `gated_rollout` returns `(verdict, None)` and makes no routing change
on BLOCK/INCONCLUSIVE, so a regression can't be promoted by mistake. This is the safety interlock that
ties the eval surface to delivery (the explicit, composable form of what `agentctl push` does). Verified
by tests that a passing candidate promotes while a regression candidate leaves routing untouched, plus a
live ALLOW-proceeds / BLOCK-skips check.
