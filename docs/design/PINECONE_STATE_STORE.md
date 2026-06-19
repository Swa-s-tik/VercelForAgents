# Design - Pinecone vector StateStore (post-1.0)

**Status:** done · **Commit:** `feat(state): Pinecone vector StateStore (alias-swap via pointer record)`

## Why

Pinecone was the one named-remaining vector backend in the roadmap. Like the Qdrant adapter, it
exists to prove the `StateStore` protocol is the only seam - it drops into `rollback.py::_stores()`
with no orchestrator change and reuses the shared `digest` formula, so a checkpoint sealed against any
backend verifies here.

## The interesting bit: alias-swap without aliases

Qdrant restore maps onto its native collection aliases. **Pinecone has no aliases**, so the adapter
models the swap with a single **pointer record**:

- each commit-scoped namespace (`<project>__<namespace>`) holds that snapshot's vectors;
- one record (`id="pointer"`) in a per-project `__live__<project>` namespace carries
  `metadata={"namespace": <live>}`.

`restore(coordinate)` upserts that pointer to the target namespace (idempotent); `live_namespace()`
reads it back. Historical namespaces are never deleted, so past state is genuinely restorable - the
same invariant the alias swap gives, and the metadata read is the live digest's input, so the digest
contract is byte-identical to every other backend.

## Selection & deps

`AGENTCTL_STATE_BACKEND=pinecone` + `PINECONE_API_KEY` / `PINECONE_INDEX`. Optional dep
(`pip install 'agentctl[pinecone]'`); the core install stays slim and the default backend is still
`json`. Pinecone is a hosted service, so (unlike Qdrant) there is no local compose profile.

## Verified

The constructor accepts an **injected `index`**, so the StateStore semantics are unit-tested against
an in-memory `FakeIndex` with **no account required** (`tests/test_pinecone.py`): digest parity with
the stub, an idempotent alias-swap restore, the other namespace staying intact (restorable), the live
pointer isolated from vector data, and the configured vector dimension. A live integration test
(`test_live_pinecone_restore`) **self-skips** unless `pinecone` is installed and a real index is
reachable - so the default suite is unaffected and the adapter is proven as far as is possible without
a hosted account.

## Boundary (honest)

The response-shape helpers (`_meta`, `_ns_count`) normalize across pinecone-client versions; the live
path is exercised only when an account is configured. The unit tests pin the adapter's *logic*; a
maintainer with a Pinecone project should run the live test once to pin the client-API surface.
