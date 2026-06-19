# Design - pgvector state stores (Workstream 1)

**Status:** done · **Commit:** `feat(state): …`

## Why

Vertical C's rollback realigns external state, but the vector and memory stores were JSON-file
**stubs**. 1.0 ships real backends - pgvector for the vector store and Postgres for an event-sourced
memory graph - without changing the rollback orchestrator or the honesty contract, and without
breaking the offline tests.

## The contract that must not change

`StateStore` (`rollback/stores/base.py`): `snapshot(coordinate)->digest`, `restore(coordinate)->digest`,
`live_digest()->digest`, with `digest(s)="sha256:"+sha256(s)[:16]`. Vertical C seals a `state_digest`
at checkpoint time and, in **Phase 3**, verifies `store.live_digest() == sealed_digest` for every
reversible pointer. So the real adapters **reuse the stub's digest formulas verbatim**:

- vector: `digest(coordinate["namespace"])` / `live_digest = digest(live_alias or "")`
- memory: `digest(f"{snapshot_seq}:{log_offset}")`

Because the formula and the namespace/coordinate strings are identical, a checkpoint sealed against
the stub verifies against the pgvector adapter and vice-versa - Phase 3 is untouched.

## Mapping the stub semantics onto Postgres

| Stub concept | pgvector / Postgres |
|---|---|
| commit-scoped collection (namespace) | `vectorstore.embeddings(project_id, collection, vector_id, version, embedding vector(8))` |
| the live alias | `vectorstore.live_alias(project_id PK, collection)` - **restore = idempotent upsert here** |
| event-sourced log | `memorystore.event_log(project_id, seq, op, origin)` (append-only) |
| HEAD `(snapshot_seq, log_offset)` | `memorystore.head(project_id PK, …)` - **restore = rewind this row** |

Restore is an O(1) idempotent pointer move; historical collections and log rows are **never
mutated**, so a rolled-past deployment's state is genuinely restorable (and its memory events sit
tombstoned past HEAD, replayable on roll-forward). Verified e2e: rolling B→A flips the alias
`v37→v36`, rewinds HEAD `1180→1000`, and leaves `log_size=1180, tombstoned=180`.

## Wiring (env-gated; default unchanged)

- `AGENTCTL_STATE_BACKEND=pgvector` + a live conn → `rollback.py::_stores()` returns
  `PgVectorStore` / `PgMemoryStore` (relational_schema stays the stub - its rollback is a
  migration-refusal, deliberately Postgres-independent). **Default (unset) → the stubs**, so all
  offline tests run with zero infra.
- The compose image is `pgvector/pgvector:pg16` (a strict superset of `postgres:16`; the full suite
  passes against it). `CREATE EXTENSION vector` lives only in `schema_vector.sql`, applied by
  `rollback schema` **only** in pgvector mode, so the default path never touches it.

## Subtleties found (and fixed)

1. **search_path / extension schema.** `schema_postgres.sql` sets `search_path=controlplane,public`,
   so `CREATE EXTENSION vector` initially installed the `vector` type into `controlplane`. A fresh
   seed/rollback connection (default search_path) then couldn't resolve `::vector`. Fix:
   `CREATE EXTENSION … SCHEMA public` + fully-qualified `public.vector(8)` / `::public.vector`.
2. **Stale tables across re-applies.** `controlplane` is dropped CASCADE each apply, which dropped
   the type-dependent `embedding` column out from under an `IF NOT EXISTS` vectorstore table. Fix:
   `schema_vector.sql` drops+recreates `vectorstore`/`memorystore` each apply, mirroring
   `schema_postgres.sql`.

## Verification

```bash
docker compose up -d postgres                                   # pgvector/pgvector:pg16
python -m pytest tests/test_pgvector.py -q                      # contract + full rollback (skips if no pgvector)
python -m pytest -q                                             # default backend = stubs, all green
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback schema && \
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback seed && \
AGENTCTL_STATE_BACKEND=pgvector agentctl rollback run aaaa1111aaaa   # alias v37->v36, HEAD 1180->1000
```

## Boundaries / post-1.0

- Qdrant / Pinecone adapters behind the same `StateStore` protocol (the protocol is already the
  only seam - a new adapter drops into `_stores()`).
- Real embeddings + ANN queries (1.0 stores a demo zero-vector; the digest contract is
  namespace-based, independent of vector content).
- Per-`graph_id` memory HEADs (1.0 uses one HEAD per project, matching the stub).
