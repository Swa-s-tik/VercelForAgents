# Design - Declarative API: the AgentDeployment CRD + reconcile (post-1.0)

**Status:** reconcile shipped; watch-loop controller + hosted GitHub App framed as remaining infra.
**Commit:** `feat(operator): AgentDeployment CRD + agentctl apply (declarative rollout)`

## Why

The ROADMAP listed "a full CRD operator + hosted GitHub App" as remaining. The honest decomposition:
the **declarative contract** (a custom resource + the reconcile that drives the control plane to match
it) is real, small, and shippable now; the **infra wrappers** around that reconcile (a watch-loop
controller process, a deployed GitHub App) are operational, not algorithmic. So this ships the
contract + a working one-shot reconcile, and identifies exactly what the wrappers add.

## What ships now

- **The CRD** - `deploy/crds/agentdeployment-crd.yaml` (`agentctl.dev/v1alpha1`, kind
  `AgentDeployment`). `spec`: `commit` (required), `weight` (0-100, canary vs full promote),
  `requireGatePR` (gate interlock), `nim`, `project`. A `status` subresource + printer columns. The
  OpenAPI schema validates the resource at the apiserver.
- **The reconcile** - `agentctl/operator/reconcile.py::reconcile_agentdeployment(conn, cr)`. A pure
  function from a desired CR to applied routing + a status. It validates apiVersion/kind, then drives
  the existing orchestrators: `gated_rollout` when `requireGatePR` is set (roll out only on ALLOW),
  else `set_canary` (canary % or full promote). Returns `phase: Live | Blocked` with
  `mode`/`routingVersion`/`gate`.
- **`agentctl apply -f <cr>.yaml`** - runs that reconcile one-shot from a YAML/JSON CR (or stdin), so
  the declarative API is usable today with no controller. Verified end-to-end: applying a 30% canary
  CR yields `Live, mode: canary, routingVersion: 2`.

## What the wrappers add (remaining)

- **Watch-loop controller.** A ~100-line [kopf](https://kopf.readthedocs.io/) handler (or a
  controller-runtime reconciler) that watches `AgentDeployment` objects and, on create/update, calls
  `reconcile_agentdeployment` and writes the returned dict to `.status`. All the logic is the
  reconcile above; the wrapper is the watch + status-write + requeue. Packaged as a Deployment in the
  Helm chart with RBAC for the CRD. (Not shipped: it needs a controller runtime dependency and a
  live-cluster e2e to verify honestly.)
- **Hosted GitHub App.** The reusable Action (`.github/actions/agentctl-gate`) already delivers
  "PR -> eval-gate -> status/check" without any hosted service. A hosted *App* adds: a deployed
  webhook endpoint, per-org installation tokens, and a UI - i.e. multi-tenant hosting of the same gate
  + reconcile. That is a deployment/ops effort (a service to run), not new control-plane code.

## Verified

`tests/test_operator.py`: the canary path (CR -> 2500 bps to the target), the full promote, the gate
interlock (a regression `requireGatePR` -> `phase: Blocked`, routing untouched), CR validation
(wrong kind / missing commit rejected), and that the CRD + sample are valid YAML with the expected
schema. Full suite 200 passed. Live: `agentctl apply -f` drives a real canary.
