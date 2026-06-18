<div align="center">

# agentctl — Vercel for AI Agents

[![CI](https://github.com/Swa-s-tik/VercelForAgents/actions/workflows/ci.yml/badge.svg)](https://github.com/Swa-s-tik/VercelForAgents/actions/workflows/ci.yml)

**Ship an agent with one command: `agentctl push`.**
Preview deploys, statistical eval-gating, a streaming gRPC gateway, and 1-click stateful
rollback — one open-source control plane instead of five stitched-together SaaS products.

`Python control plane` · `Go data plane` · `Postgres + DuckDB` · `Apache-2.0`

</div>

---

## Why this exists

Shipping an AI agent today means gluing together a different product for each concern:
one for **evals**, one for the **proxy/router**, one for **observability**, one for
**deploys/rollbacks**. None of them share a data model, so the agent lifecycle — the thing
you actually care about — is never treated as the single distributed system it is.

`agentctl` is that system. A change to a prompt, tool schema, or execution graph flows through
**one** integrated path:

> **push → isolated preview → statistical eval-gate → canary/shadow rollout → 1-click stateful rollback**

And it borrows the thing that made Vercel great for frontends: a deploy is **boring, instant,
and reversible** — except an agent deploy also has to reason about *non-deterministic quality*
(is the new version actually better?) and *external side-effects* (it sent emails, wrote vectors,
charged cards — those don't roll back when the code does). agentctl handles both.

## Architecture

```
        developer                         browser / SDK client
       │ agentctl push                   │ gRPC bidi · WebSocket edge
       ▼                                 ▼
 ┌───────────────┐                ┌──────────────────────────────────────────┐
 │ CLI (typer)   │                │      GO DATA PLANE  (gateway_core)         │
 │  pack + eval  │                │  sticky canary · shadow · token streaming  │
 └──────┬────────┘                │  routing ◀── Postgres LISTEN/NOTIFY ──┐    │
        │ webhook                 └───────────────┬───────────────────────┼────┘
        ▼                                         ▼ proxies Frame envelope │
 ┌──────────────────────────── CONTROL PLANE (Python) ────────────────────┼──┐
 │  webhook → register deployment → provision ISOLATED PREVIEW agent       │  │
 │  eval-gate: SPRT + Wilson CI  ·  rollback: atomic flip + checkpoints     │  │
 └───────────────┬──────────────────────────────────────────────────┬─────┘  │
                 ▼                                                    ▼        │
        ┌────────────────────┐                          ┌──────────────────┐  │
        │ Postgres (SoR)      │  deployments · routing   │ DuckDB (local)    │  │
        │ checkpoints · audit │  ◀── flip fires NOTIFY ──┘ eval traces       │  │
        └────────────────────┘                          └──────────────────┘  │
                 │ OTel spans (env-toggle)                                     │
                 ▼                                                             │
        ClickHouse (prod telemetry warehouse) ◀──────────────────────────────┘
```

- **Frozen `Frame` envelope** (`proto/`) carries text deltas, binary (vision/audio), tool calls,
  interrupts, and approvals — so the Python reference proxy and the Go data plane are **wire-compatible**:
  byte-identical on the frozen header (fields 1–4) and decode-interoperable both directions (`make conformance`).
- **Postgres** is the ACID system-of-record (coordinates + proof, never bulk state). A routing
  flip fires `pg_notify`, and the **live Go gateway re-routes instantly** — zero dropped streams.
- **DuckDB** is the embedded local OLAP store for eval traces (zero external deps).

## 5-minute quickstart

```bash
git clone https://github.com/Swa-s-tik/VercelForAgents && cd VercelForAgents

pip install -e .                                              # control plane + CLI
docker compose -f deploy/docker-compose.yml up -d postgres    # system-of-record
(cd agentctl/gateway_core && make build)                      # compile the Go data plane

cd examples/support_agent && agentctl push
```

That single `agentctl push` runs the **entire pipeline** and proves it on screen:

```
① pack       README.md, agent.py, prompt.yaml → commit 7ce7a54d3b08
② preview    deployment #3 · queued → building → ready · isolated agent on :57201
②′ live stream through Go data plane + side-effect
   text stream  21 TextDelta frames via the Go gateway
   arrival      first @ 33ms · last @ 639ms · spread 606ms
   buffering    none — chunks streamed incrementally
   tool call    issue_refund (side_effecting=True)
   sandbox      intercepted → mocked; real refunds issued: 0
   rollback     issue_refund sealed as side_effect/irreversible in checkpoint
   eval-gate    SPRT ALLOW @ 41/300 samples · Wilson95 [0.530, 0.804]
③ ✅ PR MERGED → promoted 100% live · https://support-agent-7ce7a54d.agents.live
```

> **What you're actually seeing.** The eval samples are drawn by a *seeded synthetic judge* from a
> configured win-rate — the statistics (Wilson CI, SPRT) run for real on those samples, but the
> preference *data* is simulated until you wire your own judge. The **②′ Go-gateway stream** only
> renders if the host-side Go binary is built (`cd agentctl/gateway_core && make build`); without it
> that stage prints `streaming proof skipped` and the rest of the pipeline runs on the Python proxy.
> The exact mapping of claim → reality is in the [status matrix](#status--whats-real).

Try a regression — the gate blocks it:

```bash
agentctl push --simulate-regression     # ⛔ PR BLOCKED (SPRT crosses the lower threshold)
```

### Or run the whole stack in one command

```bash
docker compose up --build        # Postgres + Go gateway + Python control plane
```

> This compiles the gateway *inside its container*. The `agentctl push` streaming proof launches a
> *host-side* Go binary, so to see the ②′ Go-gateway stream you still need `make build` on the host
> (otherwise that one stage is skipped — the rest of the pipeline runs regardless).

## What's inside

| Concern | How agentctl does it |
|---|---|
| **Eval-gating** | A **non-inferiority gate** on a paired win/loss/tie signal — Wilson score interval decides BLOCK/ALLOW; **Wald SPRT** stops early (an inferior agent is typically blocked in well under ~100 of 1000 samples; the exact count depends on the effect size). Fixes the naïve "win-rate < 52% AND p > 0.05" rule, which is statistically backwards. |
| **Streaming gateway** | A `grpc.aio` reference proxy (the data plane that runs by default) **plus** a **source-complete Go data plane** (`make build` to compile it) behind a frozen proto: per-session sticky canary, shadow mirroring (bounded drop-on-full in the Python reference; best-effort drop-on-error in Go today), token streaming, and a WebSocket edge where a mid-turn interrupt is a barge-in `Control` frame (stream stays open) and a client disconnect cancels the gRPC call. |
| **Stateful rollback** | Only the routing flip is a hard ACID transaction (atomic, `LISTEN/NOTIFY`); state realignment is per-pointer idempotent. Reversibility is **schema-enforced** — the system can't claim a payment is reversible. Real **pgvector** + Postgres event-sourced memory backends (`AGENTCTL_STATE_BACKEND=pgvector`). |
| **Multi-tenant RBAC** | Hashed **API keys** with `viewer/developer/admin/owner` roles, enforced at HTTP, gRPC, and CLI. Zero-config by default (a seeded bootstrap key); `AGENTCTL_REQUIRE_KEY=1` to enforce. |
| **Telemetry** | OTel spans → Postgres buffer by default; flip `TELEMETRY_BACKEND=clickhouse` for a **ClickHouse + Grafana** warehouse (optional `--profile telemetry` compose stack with provisioned dashboards). |
| **Wire conformance** | A golden-wire suite proves the Python proxy and the Go data plane are byte-identical on the frozen header + decode-interoperable (`make conformance`). |
| **Developer UX** | `agentctl push` — pack → preview → live eval → merge/block, with a rich live terminal. |

## Project layout

```
proto/                  frozen Frame envelope + AgentStream / ControlPlane services
agentctl/cli/           the typer CLI — `agentctl push` (cli/main.py)
agentctl/eval/          non-inferiority gate, sequential SPRT engine, judges
agentctl/gateway/       Python proxy, router, PG route cache, Go launcher
agentctl/gateway_core/  the compiled Go data plane (grpc-go, Postgres-routed)
agentctl/rollback/      Postgres schema, atomic flip, checkpoints, state stores
agentctl/control/       git webhook emulator        agentctl/runtime/  isolated previews + tool sandbox
examples/support_agent/ the flagship streaming, tool-calling example
docs/ARCHITECTURE_PRD.md  full design                tests/  demo/
```

## Status — what's real

The three verticals, the developer CLI, and the four production-hardening workstreams (multi-tenant
RBAC, real pgvector/memory state stores, ClickHouse + Grafana telemetry, a golden-wire proto
conformance suite) — plus a **Helm chart** and a **PyPI-ready wheel** — are runnable and tested. Every
addition is opt-in: a plain `docker compose up` + `agentctl push` still needs no API key, no pgvector,
and no ClickHouse. See `docs/ROADMAP_1_0.md` and `docs/design/*.md` for the deep-dives, and
`CHANGELOG.md` for the 1.0.0 notes.

To keep the marketing honest, here's exactly what runs on a fresh checkout vs. what's simulated or
still a scaffold:

| Capability | State |
|---|---|
| Eval-gate math (Wilson CI, Wald SPRT, McNemar/Beta/BH) | **Real & unit-tested** against hand-computed scipy values |
| Atomic routing flip + schema-enforced rollback honesty | **Real & integration-tested** (advisory lock, partial-unique index, `side_effect ⇒ irreversible` CHECK) |
| RBAC (both planes), proto conformance, pgvector/Qdrant, ClickHouse/Grafana, Helm, wheel | **Real & tested** (opt-in via env/profile) |
| Python `grpc.aio` data plane | **Real** — the proxy that runs by default |
| Go data plane (`gateway_core`) | **Source-complete; compiled & conformance-checked in CI** — but the binary isn't shipped in the tree; `make build` to run it locally |
| Demo eval samples | **Simulated** — a seeded synthetic judge from a configured win-rate; bring your own judge for real preferences |
| Demo `issue_refund` interception ("real refunds: 0") | **Illustrative** — the sandbox call is currently invoked out-of-band, not wired to the streamed tool frame |
| Header-only zero-copy forwarding · bounded Go shadow queue | **Not yet** — planned; both runtimes fully deserialize today and the Go shadow path is drop-on-error |

> **Versioning note:** the package is tagged `1.0.0` to mark the roadmap complete; treat the wire,
> `StateStore`, and auth contracts as the stable surfaces. There are no external production users yet.

## License

Apache-2.0.
