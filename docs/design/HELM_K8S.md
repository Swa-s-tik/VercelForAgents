# Design - Helm chart / Kubernetes deploy (post-1.0)

**Status:** done · **Commit:** `feat(deploy): Helm chart …`

## Why

agentctl shipped a `docker compose` story but no Kubernetes path. The Helm chart
(`deploy/helm/agentctl`) packages the core 3-tier - Postgres (SoR), the Go data-plane gateway, and
the Python control plane - so it deploys to any cluster, mirroring the default `docker compose up`
topology.

## What it deploys

`helm template` renders 7 objects: a Deployment + Service for **postgres**, **gateway**, and
**control plane**, plus a **schema-init Job**. (Telemetry/Qdrant stay compose-profile opt-ins - out
of this core chart.)

### Ordering without hooks (the load-bearing detail)

The Go gateway *requires* the schema (it reads `controlplane.routing_tables` at startup), so order
matters. Helm hooks + `--wait` would deadlock (with `--wait`, Helm waits for the gateway to be ready
before running a `post-install` schema hook, but the gateway waits for the schema). Instead the chart
uses **plain manifests + init-containers**, so everything is created at once and ordering emerges:

- **schema-init Job** → init-container waits for Postgres (`pg_isready`), then runs
  `agentctl rollback schema`.
- **gateway** and **control plane** → each has an init-container that blocks until
  `controlplane.routing_tables` exists (a `psql` poll), so their main container only starts after
  the schema is applied.

`helm install --wait` then waits for all Deployments ready + the Job complete - no hooks, no
deadlock.

## Verified end-to-end (kind)

Not just linted - actually deployed and smoke-tested on a real cluster:

```
helm lint            -> 0 failed
helm template        -> 7 valid objects
kind create cluster + kind load (controlplane/gateway/pgvector images)
helm install --wait  -> STATUS: deployed
kubectl get pods     -> control plane / gateway / postgres Running; schema-init Job Complete (1/1)
curl /healthz        -> {"status":"ok"}                       (port-forward)
kubectl exec ... agentctl auth list-keys -> bootstrap key     (schema applied + seeded in-cluster)
```

## Using it

See `deploy/helm/agentctl/README.md`. Local: build the two images, `kind load` them, `helm install`.
Real cluster: push the images to a registry and set `controlplane.image` / `gateway.image`. Enforce
auth with `--set requireKey=true --set gateway.requireKey=true`.

## Boundaries / post-1.0

- `emptyDir` for Postgres (ephemeral) - swap to a PVC/StatefulSet for persistence.
- Single replica per service; no HPA/PodDisruptionBudget/NetworkPolicy yet.
- No Ingress (use `kubectl port-forward` or add a Service of type LoadBalancer).
- A full operator (CRD-driven) is a larger future effort; this chart is the deploy primitive.
