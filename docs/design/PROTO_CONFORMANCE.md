# Design - Golden-wire proto conformance (Workstream 4)

**Status:** done · **Commit:** `feat(conformance): …`

## Why

The PRD's whole "reimplement the data plane in Go behind the frozen proto" thesis (§7) rests on one
unverified claim: that the Python reference proxy (`agentctl/gateway/proxy.py`) and the compiled Go
data plane (`gateway_core`) agree on the wire. Nothing tested it. This workstream adds the proof.

## The finding (and why the naive contract is wrong)

The first cut asserted **byte-identical full-frame** serialization (`Python.SerializeToString(deterministic=True)` == `Go proto.Marshal{Deterministic:true}`). It **failed** - and correctly so:

> Protobuf "deterministic" marshaling is **per-runtime canonical, not cross-runtime canonical.**

For any frame that carries both a `oneof` payload **and** a higher-numbered field (the `attributes`
map, field 16), the two runtimes order fields differently:

- **Python** emits fields in ascending number order: `…header… → payload(5) → attributes(16)`.
- **Go** emits the `oneof` payload **after** the map: `…header… → attributes(16) → payload(5)`.

The bytes differ; the *logical message* is identical (field order is not significant in protobuf).
Frames without a map (e.g. `tool_call`) happen to match, which is why only the `attributes` frames
drifted. Asserting byte-identity would have been a test that protobuf never promises to pass.

## The contract we actually assert

agentctl's gateway forwards a frame by parsing only its **header** and passing the original payload
bytes through untouched (header-only forwarding, PRD §4). It never re-encodes a forwarded frame. So
the property that must hold is not "both runtimes re-encode identically" - it's:

1. **The frozen header (fields 1-4: `session_id, stream_id, seq, direction`) is byte-identical
   across runtimes.** Header fields always serialize first, in ascending order, with no oneof/map to
   reorder - so this *is* guaranteed, and it's exactly what the cheap header-parse forwarding relies
   on. We marshal a header-only `Frame` on each side and assert equal bytes (`header_hex`).
2. **Lossless cross-runtime decode (both directions).** Each runtime decodes the other's wire into
   the same logical frame:
   - Go decodes Python's `golden_hex` and re-marshals (Go-canonical) to the same bytes Go builds
     from the spec.
   - Python decodes Go's wire (`conformance_go_wire.json`) and re-marshals (Python-canonical) to the
     same bytes Python builds from the spec.

Together these guarantee the two planes are wire-**interoperable** - which is the real requirement -
and that the frozen-header forwarding fast path is byte-exact.

## Mechanism

- **Single source of truth:** `tests/fixtures/conformance_frames.json` - language-neutral frame
  specs (frozen header + every oneof payload + enums + bytes + a multi-key `attributes`
  map-ordering stress case) plus committed `golden_hex` (Python wire) and `header_hex`.
- **Builders mirror each other:** `tests/conformance_frames.py::build_frame` and
  `gateway_core/internal/gateway/conformance.go::BuildFrame` construct a `Frame` from the same spec.
- **Python test** `tests/test_conformance.py`: locks `golden_hex`, asserts `header_hex`, round-trips,
  and decodes Go's wire.
- **Go test** `gateway_core/internal/gateway/conformance_test.go`: asserts `header_hex` byte-identity
  and decodes Python's wire.
- **`make fixtures`** (`cmd/genfixtures`) verifies the Go side of the contract and emits
  `conformance_go_wire.json` for Python's symmetric decode check.
- Regenerate goldens after an intentional proto change: `python tests/conformance_frames.py`.

## Verification (CI-less, two commands)

```bash
python -m pytest tests/test_conformance.py -q
cd agentctl/gateway_core && make fixtures && make conformance
```

Green = the frozen header is byte-identical and both runtimes decode each other losslessly across
all 9 vectors. If either runtime's encoding drifts (a proto edit, a library bump that changes
canonical order), the relevant assertion goes red.

## Control-plane + Health messages (post-1.0)

The suite now also covers the `ControlPlane` service messages (`RouteTable`/`Backend`/`ShadowPolicy`,
`ResolveRouteRequest`, `WatchRequest`, `TelemetryBatch`/`Event`/`Ack`) and the `Health` messages -
exercising nested messages, repeated fields, a `map<string,double>` (a new wire shape vs the
`Frame`'s string map), and enums. There's no frozen header here, so the contract is **cross-runtime
decode interop both directions** (Go decodes the Python golden; Python decodes Go's wire). Builders:
`tests/conformance_control.py` + `gateway_core/internal/gateway/conformance_control.go`; fixtures
`tests/fixtures/conformance_control{,_go_wire}.json`; verified by `TestControlConformance` (Go) and
`test_control_*` (Python). Regenerate: `python tests/conformance_control.py` + `make fixtures`.

## Boundaries / future

- The whole agentctl wire contract (Frame hot path + control plane + Health) is now vectored.
- If a future need for a *canonical cross-runtime byte form* arises (e.g. content-addressing frames),
  it must be built on an explicit field-ordered encoder, not on `deterministic=true`. Documented here
  so nobody re-introduces the naive assumption.
