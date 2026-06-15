# agentctl — Architecture PRD

**A unified, open-source GitOps control plane for AI agents ("Vercel for AI agents").**

## 1. Problem & thesis

Shipping an agent today means stitching together 4–5 disconnected SaaS products: one for
evaluation, one for proxy/routing, one for observability, one for deploys/rollbacks.
They don't share a data model, so the agent lifecycle is never treated as the single
distributed system it actually is.

`agentctl` is one cohesive control plane over that lifecycle. A change to a prompt, tool
schema, or execution graph flows through **one** integrated path: preview → statistical
eval-gate → canary/shadow rollout → 1-click rollback that also realigns external state.

This document is the finalized design of the prototype in this repo. It is a runnable
**skeleton that proves the three hardest concepts**, not a finished platform; clearly-marked
stubs and the 3-phase roadmap (§8–9) describe the path to release.

## 2. System topology

```
                              ┌──────────── developer ───────────┐
                              │   git push (prompt/graph/schema)  │
                              └───────────────┬───────────────────┘
                                              ▼
   browser ──WebSocket──▶ ┌──────────────────────────────────────────────┐
   (interrupts, HITL)     │              EDGE (Vertical B)               │
                          │   ws_bridge: WS  ⇄  gRPC bidi (Frame)        │
                          └───────────────┬──────────────────────────────┘
                                          │ gRPC bidirectional streaming
                                          ▼
   ┌───────────────────────────── GATEWAY (Vertical B) ───────────────────────────────┐
   │  router: per-session sticky weighted canary   proxy: fan-out + bounded queues     │
   │     primary ──┐ (lossless)        shadow ──┐ (lossy, drop-on-full, discarded)      │
   └───────────────┼───────────────────────────┼──────────────────────────────────────┘
                   ▼                            ▼
        ┌──────────────────┐         ┌──────────────────┐      preview agents register
        │ agent vA (w=90)  │         │ shadow agent      │      here as routable backends
        │ agent vB (w=10)  │         │ (mock side-effects)│     (Vertical A previews)
        └──────────────────┘         └──────────────────┘
                   ▲ reads routing table (low-latency)        ▲ writes eval traces
                   │  LISTEN/NOTIFY + cache                   │
   ┌───────────────┴───────────── CONTROL PLANE ─────────────┴──────────────────────────┐
   │  Postgres (Vertical C): deployments · routing_tables(1-live) · checkpoints ·         │
   │     state_pointers · rollbacks · audit_log · otel_spans   ──┐                        │
   │  DuckDB (Vertical A, local): eval_run · eval_sample · trace_event · gate_result      │
   └─────────────────────────────────────────────────────────────┼──────────────────────┘
                                                                  │ TELEMETRY_BACKEND=clickhouse
                                                                  ▼
                                                   ClickHouse (prod telemetry warehouse)
```

### State split (deliberate)

| Store | Role | Holds |
|---|---|---|
| **Postgres** | ACID system-of-record | deployments, the live routing table, checkpoint **coordinates + proof**, audit. Never bulk state. |
| **DuckDB** | local OLAP, embedded, zero-dep | eval samples + traces for local/preview (the "5-minute setup"). |
| **ClickHouse** | prod telemetry warehouse | heavy OTel spans at scale; switched in by one env var. Postgres is only a short buffer. |

## 3. Vertical A — Probabilistic GitOps Engine (eval-gating)

**Git-triggered previews** provision an ephemeral, isolated agent endpoint per commit
(container-per-sha; CI-webhook trigger recommended over a server-side git hook — richer
payload, retries, fits the 5-minute setup). The preview registers as a routable backend
(§4) and runs eval suites whose traces land in DuckDB.

**Eval-gating — the spec's rule, corrected.** The brief proposed *"block if win-rate < 52%
AND p-value > 0.05."* This is statistically incoherent: `p > 0.05` means *no evidence of a
difference*, so blocking on it blocks good-but-noisy candidates and passes regressions the
moment they're noisy; and a bare 52% ignores sample size. We replace it with a
**non-inferiority gate** on a *paired* WIN/LOSS/TIE preference signal (candidate vs main on
the same item):

- **Wilson score interval** `[lo, hi]` on the win-rate drives the decision (correct at small
  n and near 0/1). Exact **McNemar** (`scipy.binomtest` on discordant pairs) and a
  **Beta-Binomial** posterior `P(θ>nim)` are reported *alongside* but never gate.
- **Decision** (margin `nim`, default 0.50): `INSUFFICIENT_DATA` if `n<n_min`; **BLOCK if
  `hi < nim`** (whole CI below margin → confident regression); **ALLOW if `lo ≥ nim`**;
  else `INCONCLUSIVE`. Set `nim = 0.52` for **superiority mode** — the only sound reading
  of the spec's "52%".
- Ties fold via `tie_mode` (default `halve`). Across the many suites a PR runs,
  **Benjamini–Hochberg FDR** controls false regressions; the PR blocks if any suite blocks.
- Peeking is avoided by a **fixed-horizon** evaluation (one verdict at suite completion);
  anytime-valid confidence sequences are the roadmap upgrade.

**Tool mocking** intercepts external side-effects during preview at the single tool-dispatch
seam — cassette replay keyed by `(tool, schema_hash, args)` with a schema-driven fallback so
a preview never sends a real email or charges a card.

**DuckDB schema:** `eval_run` (commit/baseline/suite/judge), `eval_sample` (paired
preference + scores), `trace_event` (per-arm latency/tokens/cost, `mocked` flag, `otel_*`
ids = the prod boundary), `gate_result` (cached verdict).

## 4. Vertical B — Core Streaming Gateway

**The `Frame` envelope is the contract** (`proto/envelope.proto`): a `oneof payload`
(TextDelta · BinaryChunk · ToolCall · ToolResult · Control · Approval{Req,Res} · TurnEnd ·
StreamError). **Header fields 1–4 (`session_id, stream_id, seq, direction`) are frozen
forever** so the proxy forwards on a cheap header-only parse and a future Go/Rust data plane
stays wire-compatible. `BinaryChunk` carries modality/codec/dims for vision/audio/TensorRT;
`ToolCall.side_effecting` is the one bit that makes shadow isolation tractable.

- **gRPC bidirectional streaming** everywhere internal (`AgentStream.Converse`); the WS edge
  is a 1:1 translation for browsers.
- **Canary = per-session sticky** weighted pick (`hash(session_id) ⊕ table.version`), pinned
  for the session. Per-message routing would corrupt conversational state and break approval
  round-trips. Verified ~90/10 over 100 sessions.
- **Shadow = lossy fan-out**: offered to a bounded queue, **drop-on-full**, responses
  discarded. A slow/failing shadow can never throttle or fail the primary. Shadow tool calls
  are answered by the mock layer, never real tools.
- **Backpressure asymmetry (the key transport invariant):** primary is lossless
  (propagated), shadow is lossy (absorbed by dropping).
- **Edge**: `INTERRUPT` (barge-in) keeps the stream open and cuts generation mid-flight;
  **WS close → gRPC `cancel()`**; HITL approval round-trips with a gateway-synthesized DENY
  on timeout.

The gateway reads the routing table (owned by Vertical C) via a cache; it is never the
source of truth.

## 5. Vertical C — Stateful Rollback Engine

**The State-Sync Paradox:** rolling back *code* (git) doesn't roll back *state* (vectors,
schema, memory). A deployment is a `(code, state)` pair; rollback must realign both — or be
honest about what it can't.

- **Postgres holds coordinates + proof, never bulk state.** `deployments` (git sha = the
  spine), versioned `routing_tables` + `routing_rules`, `checkpoints` + `state_pointers`,
  `memory_sync_pointers`, `rollbacks`, append-only `audit_log`, `otel_spans`.
- **Only the routing flip is a hard ACID transaction** — advisory-locked, demote-then-insert,
  guarded by **`one_live_routing_per_project` partial-unique index** (the gateway never reads
  a torn edit) + transactional `pg_notify`. State realignment is **per-pointer idempotent**,
  not a distributed transaction (no 2PC across Postgres + a vector DB + Stripe).
- **Reversibility is schema-enforced:** `CHECK (side_effect ⇒ irreversible)` — the system
  *cannot* record a payment as reversible. Three restore strategies: vector = namespace/alias
  swap; memory = event-sourced HEAD rewind; schema = **never auto-run a data-lossy
  down-migration** (expand-contract; flag `forward_fix`). Irreversible side-effects are
  compensated (idempotency-keyed) or flagged.
- **Honesty over magic:** the rollback restores every reversible pointer and **enumerates
  every non-reversible one in `rollbacks.unrollbackable`**, reporting `compensating` (partial)
  rather than a fake `completed`.

## 6. Cross-cutting: telemetry boundary

Spans are modeled on the OTel data model (`otel_spans` mirrors trace/span/parent/kind/
attributes). `TELEMETRY_BACKEND=postgres` (default, short buffer) flips to `clickhouse` via
env var (`CLICKHOUSE_DSN` / `OTEL_EXPORTER_OTLP_ENDPOINT`) with no schema change — heavy logs
never bloat the ACID core.

## 7. The Go/Rust reimplementation seam

The Python gateway is the *reference* data plane. The production data plane can be rewritten
in Go (goroutine-per-stream, zero-copy `[]byte` forward) or Rust (tonic + tokio) behind the
**frozen proto** + the header-only-parse forwarding design. Agents, the WS bridge, and the
control plane are unchanged; a golden-wire conformance suite (roadmap) guards parity.

## 8. What is real vs stubbed

| Real (proven runnable) | Stubbed (named seam, deferred) |
|---|---|
| Eval gate math + DuckDB store + multi-suite BH (14 tests) | live LLM judge; container preview provisioning |
| Postgres schema + atomic flip + idempotent rollback + audit (integration test) | real vector/memory/schema adapters (in-proc stubs honor `StateStore`) |
| gRPC bidi proxy: sticky canary + shadow + interrupt; WS edge (router tests + 2 demos) | Postgres-fed route cache (`WatchRoutes`/LISTEN-NOTIFY); OTLP export; auth/TLS |

## 9. Roadmap to OSS release

- **Phase 1 — Foundations (this repo).** Monorepo, PRD, both schemas, 3 runnable prototypes,
  tests, docker-compose, demos. The 5-minute local setup.
- **Phase 2 — Integration & hardening.** Wire the full flow (push → preview registers backend
  → canary → eval-gate → 1-click rollback). Replace stubs: container previews + CI webhook;
  `WatchRoutes` + LISTEN/NOTIFY cache; OTLP exporter; real tool-mock runtime; auth/TLS;
  anytime-valid eval; proto conformance suite.
- **Phase 3 — Production & launch.** Go/Rust gateway data plane behind the frozen proto;
  ClickHouse + Grafana/Tempo; Helm/operator; multi-tenant RBAC; managed vector/memory
  adapters (Pinecone/Qdrant/pgvector); docs site; Apache-2.0 governance + first tagged release.

## 10. Deliverables map

| Brief deliverable | Where |
|---|---|
| `ARCHITECTURE_PRD.md` + topology | this file |
| Postgres DDL | `agentctl/rollback/schema_postgres.sql` |
| DuckDB schema | `agentctl/storage/schema_duckdb.sql` |
| Working prototype(s) | all three verticals + `demo/` + `tests/` |
| Roadmap | §9 |
