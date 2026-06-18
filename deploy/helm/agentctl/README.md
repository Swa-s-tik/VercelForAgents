# agentctl Helm chart

Deploys the agentctl core 3-tier — Postgres (system-of-record), the Go data-plane gateway, and the
Python control plane — to Kubernetes. Mirrors the default `docker compose up` topology. (Telemetry
and Qdrant stay compose-profile opt-ins; they're out of this core chart.)

## Quickstart (local, kind)

```bash
# 1. build the two agentctl images from the repo root
docker build -f deploy/Dockerfile.controlplane -t agentctl/controlplane:dev .
docker build -f deploy/Dockerfile.gateway      -t agentctl/gateway:dev .

# 2. a local cluster + load the images (and the Postgres image) into it
kind create cluster --name agentctl
kind load docker-image agentctl/controlplane:dev agentctl/gateway:dev pgvector/pgvector:pg16 --name agentctl

# 3. install
helm install agt deploy/helm/agentctl --wait --timeout 5m

# 4. smoke test
kubectl port-forward svc/agt-agentctl-controlplane 8088:8088 &
curl localhost:8088/healthz        # {"status":"ok"}
```

On a real cluster, push the images to a registry and set
`--set controlplane.image=... --set gateway.image=... --set *.pullPolicy=IfNotPresent`.

## How it comes up

`helm install --wait` creates everything at once; ordering is enforced by init-containers, so there
are no hooks and no deadlocks:

- **schema-init Job** waits for Postgres (`pg_isready`), then runs `agentctl rollback schema`.
- **gateway** and **control plane** each wait (init-container) until `controlplane.routing_tables`
  exists, so they only start after the schema is applied (the Go gateway requires it).

## Key values

| Key | Default | Notes |
|---|---|---|
| `postgres.image` | `pgvector/pgvector:pg16` | superset of pg16; the pgvector state backend needs it |
| `controlplane.image` / `gateway.image` | `agentctl/{controlplane,gateway}:dev` | build + load or push these |
| `gateway.projectId` | demo project UUID | the tenant the gateway serves |
| `requireKey` / `gateway.requireKey` | `false` | enforce API keys on HTTP / gRPC |

See `values.yaml` for the full set.
