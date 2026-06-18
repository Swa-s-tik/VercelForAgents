# Design — Qdrant vector state store (post-1.0)

**Status:** done · **Commit:** `feat(state): Qdrant vector adapter …`

## Why

1.0 shipped one real vector backend (pgvector). The thesis behind the `StateStore` protocol is that
a *second* backend drops in with **no orchestrator change**. This proves it — and Qdrant is the
ideal demonstration because it has **native collection aliases**, so the alias-swap rollback maps
onto a first-class Qdrant primitive instead of an emulated one.

## Mapping

| agentctl concept | Qdrant |
|---|---|
| commit-scoped namespace | a collection `<project_id>__<namespace>` |
| the live alias | a per-project alias `live__<project_id>` |
| restore (alias swap) | `update_collection_aliases([DeleteAlias, CreateAlias])` — atomic |
| `snapshot` / `live_digest` | `digest(namespace)` — the **same** formula as every other backend |

`restore` re-points the alias; historical collections are never touched, so a rolled-past
deployment's vectors stay intact (verified: after B→A, both `proj-a1-ns-v36` and `…v37` still
exist, alias → v36, count 50). Because the digest formula is reused verbatim, a checkpoint sealed
against the stub or pgvector verifies against Qdrant — Vertical C's Phase-3 check is unchanged.

## Scope (deliberately narrow)

Qdrant swaps **only the vector store**. Memory + relational-schema stay the file-backed stubs, so
the Qdrant path adds no new SQL coupling — it's purely "a second managed vector backend behind the
protocol." `_stores()` gains one branch:

```python
if STATE_BACKEND == "qdrant":
    return {"vector_store": QdrantStore(project_id),
            "memory_graph": MemoryGraphStub(), "relational_schema": SchemaStoreStub()}
```

## Wiring & opt-in

- `AGENTCTL_STATE_BACKEND=qdrant` + a reachable Qdrant at `QDRANT_URL` (default
  `http://localhost:6333`). Default backend is still `json`, so nothing changes by default.
- Optional dep: `pip install 'agentctl[qdrant]'` (`qdrant-client`). The core install stays slim;
  `tests/test_qdrant.py` self-skips when the client isn't installed (so CI, which uses the base
  install, never needs Qdrant).
- Optional compose service: `docker compose --profile qdrant up -d` (Qdrant on 6333/6334).

## Subtleties found

- The alias op field is `delete_alias`/`create_alias` (not `delete`/`create`) in qdrant-client
  1.18 — the CREATE-only seed path masked it; the rollback's DELETE+CREATE swap surfaced it.
- Client/server minor-version skew triggers a warning; the adapter sets `check_compatibility=False`
  since it uses only the stable collection/alias/upsert API.

## Verification

```bash
docker compose --profile qdrant up -d
pip install 'agentctl[qdrant]'
AGENTCTL_STATE_BACKEND=qdrant agentctl rollback schema && \
AGENTCTL_STATE_BACKEND=qdrant agentctl rollback seed && \
AGENTCTL_STATE_BACKEND=qdrant agentctl rollback run aaaa1111aaaa   # alias v37→v36, count 80→50
python -m pytest tests/test_qdrant.py -q
```

## Post-1.0 remaining

- A Pinecone adapter (same protocol; the only open item from the original "managed vector adapters"
  line).
- Real embeddings + ANN search (the demo stores a zero-vector; the rollback contract is
  namespace/alias-based, independent of vector content).
