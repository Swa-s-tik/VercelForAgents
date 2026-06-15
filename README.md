# agentctl

A unified, open-source **GitOps control plane for AI agents** — eval-gating, a streaming
gRPC gateway, and stateful rollback in one cohesive system instead of 4–5 stitched-together
SaaS products. See [docs/ARCHITECTURE_PRD.md](docs/ARCHITECTURE_PRD.md) for the full design.

> Prototype status: a runnable skeleton proving the three hardest concepts (clearly-marked
> stubs elsewhere). Python-first; the gRPC data plane is designed to be reimplemented in
> Go/Rust behind a frozen proto contract.

## 5-minute local setup

```bash
pip install -e .                                   # scipy/numpy/fastapi already present
docker compose -f deploy/docker-compose.yml up -d postgres
python -m grpc_tools.protoc -I proto \
  --python_out=agentctl/gen --grpc_python_out=agentctl/gen proto/*.proto   # regenerate stubs
python demo/make_fixtures.py                       # generate eval fixtures
./demo/run_all.sh                                  # run all three verticals end-to-end
```

## The three verticals

**A · Eval-gating** — a statistically sound, non-inferiority merge gate (Wilson CI drives
BLOCK/ALLOW/INCONCLUSIVE/INSUFFICIENT_DATA; exact McNemar + Beta-Binomial reported; BH-FDR
across suites). Corrects the brief's incoherent `win-rate<52% AND p>0.05` rule.

```bash
agentctl eval ingest --run demo/fixtures/candidate.jsonl --baseline demo/fixtures/main.jsonl --commit good --pr 100
agentctl gate --pr 100        # -> ALLOW (exit 0)
agentctl eval ingest --run demo/fixtures/candidate_regression.jsonl --baseline demo/fixtures/main.jsonl --commit regr --pr 101
agentctl gate --pr 101        # -> BLOCK (exit 1)
```

**B · Streaming gateway** — gRPC bidi reverse proxy with per-session sticky canary, shadow
mirroring (drop-on-full, discarded), and a WebSocket edge with mid-stream interrupts.

```bash
python demo/gateway_demo.py   # 90/10 canary + shadow + gRPC interrupt
python demo/ws_demo.py        # WebSocket -> gRPC interrupt round-trip
# or run servers manually: agentctl agent --tag vA --port 50051 ; agentctl gateway --port 50050
```

**C · Stateful rollback** — Postgres system-of-record; only the routing flip is ACID
(partial-unique one-live-table + NOTIFY); state realignment is idempotent; reversibility is
schema-enforced; non-reversible state is surfaced, never faked.

```bash
agentctl rollback schema && agentctl rollback seed
agentctl rollback run aaaa1111aaaa   # flip to A + restore reversible state + flag the rest
agentctl rollback audit
```

## Tests

```bash
python tests/test_gate.py        # 14 gate-statistics tests
python tests/test_router.py      # canary distribution + stickiness
python tests/test_rollback.py    # atomic flip + restore + audit (needs Postgres)
```

## Layout

```
proto/                 frozen Frame envelope + AgentStream/ControlPlane services
agentctl/eval/         non-inferiority gate, ingest, judge, runner (Vertical A)
agentctl/storage/      DuckDB store + schema (Vertical A)
agentctl/gateway/      router, proxy, shadow, route cache (Vertical B)
agentctl/edge/         WebSocket->gRPC bridge (Vertical B)
agentctl/rollback/     Postgres schema, routing flip, rollback orchestrator, stores (Vertical C)
demo/  tests/  docs/   demos, tests, the architecture PRD
```

License: Apache-2.0.
