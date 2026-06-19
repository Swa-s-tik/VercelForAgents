# agentctl - local setup (fresh-laptop guide)

A bulletproof, copy-paste path from a clean machine to a running `agentctl push`. Should take
about five minutes, most of it Docker pulling images.

---

## 1. Prerequisites

Install these three. Everything else is handled for you (the Go data plane ships with vendored
protobuf stubs, so you do **not** need `protoc`).

| Tool | Version | Check | Notes |
|---|---|---|---|
| **Docker** + Compose v2 | recent | `docker compose version` | Runs Postgres, the Go gateway, and the Python control plane. Make sure the daemon is running. |
| **Python** | 3.10+ | `python3 --version` | For the control plane + the `agentctl` CLI. A venv is strongly recommended. |
| **Git** | any | `git --version` | To clone the repo. |

> **Go is optional - with one caveat.** The full stack runs in Docker (the gateway is compiled inside
> its image), so the control plane, eval-gate, routing flip, and rollback all work with no local Go.
> **But** the demo's ②′ "stream through the Go gateway" stage launches a *host-side* Go binary that the
> Docker build doesn't produce - to see that one stage you need a local Go toolchain (`go >= 1.22`) and
> the optional `make build` step at the end. Without it, that stage prints `streaming proof skipped` and
> the rest of the pipeline runs on the Python proxy.

---

## 2. The 5-minute spin-up

### a. Clone

```bash
git clone https://github.com/Swa-s-tik/agentctl.git
cd agentctl
```

### b. Install the CLI (in a virtualenv)

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .                     # installs the control plane + the `agentctl` command
```

Verify the CLI is on your PATH:

```bash
agentctl --help
```

### c. Bring up the whole stack in one command

From the repo root:

```bash
docker compose up --build
```

This builds and starts three services wired together by `docker-compose.yml`:

- **postgres** - the ACID system-of-record (deployments, routing, checkpoints, audit), exposed on
  host port **5433**.
- **schema-init** - a one-shot job that applies the control-plane schema (`agentctl rollback
  schema`) and exits; the gateway and control plane wait for it to finish.
- **gateway** - the compiled **Go data plane**, listening on **50050**, routed by Postgres
  LISTEN/NOTIFY.
- **controlplane** - the Python control plane / webhook receiver on **8088**.

Leave that running and open a second terminal for the demo (re-activate the venv there:
`source .venv/bin/activate`).

> **Tip:** add `-d` (`docker compose up --build -d`) to run it detached and get your terminal back;
> follow logs with `docker compose logs -f gateway`. Tear everything down with `docker compose down`
> (add `-v` to also wipe the Postgres volume for a truly clean slate).

---

## 3. The "aha" moment - run the demo

The flagship example is a streaming, tool-calling customer-support agent. With the stack up, in your
second terminal:

```bash
cd examples/support_agent
agentctl push
```

`agentctl push` runs the **entire pipeline** against this agent and proves each stage on screen.
Watch for, in order:

1. **① pack** - the agent (`agent.py`, `prompt.yaml`, `README.md`) is tarred + hashed into a
   content-addressed commit.
2. **② preview** - a deployment is registered and an **isolated preview agent** boots on its own
   port (`queued → building → ready`).
3. **②′ live stream through the Go data plane** - the part to actually watch *(requires the host-side Go
   binary from `make build`; otherwise this stage prints `streaming proof skipped` and the pipeline
   continues on the Python proxy)*:
   - **incremental streaming** - ~21 `TextDelta` frames arrive spread over several hundred
     milliseconds (first @ ~30ms, last @ ~600ms). Nothing is buffered; you're seeing tokens stream
     through the real Go gateway.
   - **side-effect handling** - the agent emits an `issue_refund` tool call marked side-effecting, which
     is **sandbox-mocked** in preview (`real refunds issued: 0`) and sealed as
     `side_effect / irreversible` in the rollback checkpoint. (Today the sandbox call is invoked
     out-of-band rather than wired to the streamed tool frame - the seal is real, the interception is
     illustrative.)
4. **the eval-gate, live** - the **Wilson 95% confidence interval bar updates in place** as paired
   samples stream in, and **Wald's SPRT** stops early and prints its call, e.g.
   `SPRT ALLOW @ 41/300 samples · Wilson95 [0.530, 0.804]`. *(The samples come from a seeded synthetic
   judge - the statistics are real; the preference data is simulated until you wire your own judge.)*
5. **③ routing promotion** - because the candidate is non-inferior, you get
   `✅ PR MERGED → promoted 100% live`. Under the hood that's the **atomic routing flip** (advisory
   lock + partial-unique index), and its `pg_notify` re-routes the live Go gateway over
   LISTEN/NOTIFY with zero dropped streams.

### See the gate do its job - block a regression

```bash
agentctl push --simulate-regression
```

Same pipeline, but the candidate is deliberately worse. The Wilson interval falls below the margin,
the **SPRT crosses the lower threshold**, and you get `⛔ PR BLOCKED` - usually after only a few
dozen samples, demonstrating the early-stopping compute saving. That contrast - MERGED on a good
agent, BLOCKED on a bad one, both decided statistically and live - is the whole pitch in two
commands.

---

## Opt-in 1.0 capabilities

All of these are off by default - the quickstart above needs none of them.

### Multi-tenant RBAC (API keys)
Auth is permissive by default (no key → the seeded **bootstrap** owner of the demo project), so
`agentctl push` just works. To use a real key or enforce auth:

```bash
agentctl auth create-key --role developer        # prints a secret once
agentctl push --api-key actl_xxx                 # or export AGENTCTL_API_KEY
AGENTCTL_REQUIRE_KEY=1 agentctl push             # now a valid key is mandatory
```

> The bootstrap dev key is `actl_dev_bootstrap_0000000000000000` (owner, demo project). It exists
> only to make local use zero-config - **rotate/revoke it before any shared deployment** (see
> `SECURITY.md`).

### Real pgvector / memory state backend
```bash
docker compose up -d postgres                    # the pgvector/pgvector:pg16 image
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback schema
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback seed
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback run aaaa1111aaaa   # alias v37→v36, HEAD 1180→1000
```

### ClickHouse + Grafana telemetry
```bash
docker compose --profile telemetry up -d         # adds ClickHouse (:8123/:9000) + Grafana (:3001)
TELEMETRY_BACKEND=clickhouse agentctl ...         # spans land in agentctl.otel_spans
# open http://localhost:3001  (anonymous admin) for the provisioned dashboard
```
A plain `docker compose up` (no `--profile telemetry`) never starts these and keeps
`TELEMETRY_BACKEND=postgres`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `agentctl: command not found` | Activate the venv (`source .venv/bin/activate`) and re-run `pip install -e .`. |
| Port `5433`, `50050`, or `8088` already in use | Stop the conflicting process, or edit the host-side port mappings in `docker-compose.yml`. |
| `push` can't reach Postgres / the gateway | Confirm `docker compose ps` shows all services up and `schema-init` **completed** (not errored); give Postgres a few seconds to pass its healthcheck on first boot. |
| Want a clean slate | `docker compose down -v` removes the Postgres volume so the next `up` re-runs `schema-init` fresh. |

---

## Optional: compile the Go data plane outside Docker

You don't need this for the demo (Docker compiles it for you), but if you want a local binary:

```bash
# requires go >= 1.22
cd agentctl/gateway_core
make build        # protobuf stubs are vendored - no protoc needed
./bin/gateway     # honors AGENTCTL_GW_PORT and AGENTCTL_PG_DSN
```

`make build` only compiles; the generated gRPC stubs are committed under `gateway_core/gen/`. Run
`make proto` to regenerate them, which *does* require `protoc` + the Go plugins.
