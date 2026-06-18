# Contributing to agentctl

Thanks for helping build the open-source control plane for AI agents. This guide gets you from a
clean checkout to a green test run, and explains the few rules that keep the two-runtime system
honest.

## Dev setup

```bash
git clone https://github.com/Swa-s-tik/VercelForAgents.git && cd VercelForAgents
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                                    # control plane + CLI
docker compose -f deploy/docker-compose.yml up -d postgres   # pgvector/pgvector:pg16 (SoR)
(cd agentctl/gateway_core && make build)            # compile the Go data plane
```

Go (>= 1.22) is only needed for the data plane and the conformance suite. Postgres is the
`pgvector/pgvector:pg16` image (a strict superset of stock pg16; the pgvector state backend needs
it).

## Running the tests (two runtimes)

agentctl is a Python control plane **and** a Go data plane, so "green" means both:

```bash
python -m pytest -q                                 # Python: all suites (needs Postgres up)
cd agentctl/gateway_core && make build && make conformance
```

- `tests/test_conformance.py` + `make conformance` are the **golden-wire** gate: they prove the
  Python and Go runtimes are wire-compatible on the frozen `Frame` envelope. Run both after touching
  anything under `proto/`, `agentctl/gen/`, `agentctl/gateway/`, or `gateway_core/`.
- The optional backends self-skip when their infra is absent (`test_pgvector` needs the pgvector
  image; the ClickHouse exporter test is infra-free).

## Rules that keep the system honest

1. **The proto is the single source of truth.** `proto/*.proto` defines the wire. The header fields
   1–4 (`session_id, stream_id, seq, direction`) are **frozen forever**. If you change a message,
   regenerate the goldens (`python tests/conformance_frames.py`) and run the conformance suite on
   both runtimes — a drift there is a breaking change.
2. **Don't weaken the honesty guard.** The `side_effects_are_irreversible` CHECK and the per-pointer
   idempotent rollback are load-bearing. A rollback that touched irreversible state reports
   `compensating` and enumerates what it couldn't undo — it never fakes `completed`.
3. **Keep the zero-config path working.** New capability is opt-in (an env flag or a compose
   profile). A plain `docker compose up` + `agentctl push` must work with no API key, no pgvector,
   no ClickHouse. The existing test suite is the regression guard — keep it green.
4. **Backends drop in behind their seam.** New state stores implement the `StateStore` protocol and
   register in `rollback.py::_stores()`; new telemetry backends branch in
   `telemetry/exporter.py::_make_exporter`. Don't thread backend-specifics through the orchestrator.

## Commits & PRs

- Conventional-commit style subjects (`feat(...)`, `fix(...)`, `docs(...)`), imperative mood.
- One logical change per PR; update the relevant `docs/design/*.md` and `CHANGELOG.md` in the same PR
  (docs are a first-class deliverable, not an afterthought).
- AI-assisted commits include a trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Run both runtimes' tests before pushing.

## Where things live

See `docs/ARCHITECTURE_PRD.md` for the design, `docs/ROADMAP_1_0.md` for status, and
`docs/design/*.md` for per-workstream deep-dives.
