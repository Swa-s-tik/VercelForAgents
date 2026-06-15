# gateway_core — Go data-plane gateway (scaffold)

The high-performance native data plane that will eventually **lift-and-shift the Python
`grpc.aio` proxy** (`agentctl/gateway/`) — behind the *same frozen Protobuf contract*
(`../../proto`). Agents, the WebSocket edge, and the control plane are unchanged; only the
hot-path proxy is reimplemented.

> Status: **scaffold, not compiled here** (no Go toolchain in this environment). The code is
> idiomatic grpc-go and complete enough as the baseline handler boilerplate. `make build`
> generates stubs from the protos and compiles it on a machine with Go + protoc.

## Layout
```
cmd/gateway/main.go              server bootstrap (:50050, 64MB msg limits)
internal/gateway/router.go       sticky weighted canary — mirrors router.py (sha1 hash ^ version)
internal/gateway/proxy.go        bidi streaming reverse proxy: fan-out, shadow, canary_arm tag
gen/acpv1/                        generated stubs (make proto)  [gitignored]
Makefile                         proto codegen + go build
```

## Build
```bash
# prerequisites: go>=1.22, protoc, protoc-gen-go, protoc-gen-go-grpc
make build      # protoc ../../proto/*.proto -> gen/acpv1, then go build
make run        # listen on :50050
```

## Parity with the Python proxy (the invariants a reimpl must preserve)
- **Frozen `Frame` envelope**: header fields 1-4 (`session_id, stream_id, seq, direction`) are
  stable forever, enabling a header-only-parse fast path. `router.go` mirrors `weighted_pick`
  exactly (sha1 of session id, big-endian first 8 bytes, XOR table version, cumulative ranges
  sorted by backend id) so the same session lands on the same arm in Go and Python.
- **Per-session sticky canary**, **lossy shadow fan-out** (drop on send error, responses
  discarded, never block the primary), **lossless primary** path.
- `Health` feeds the routing table's readiness set.

## The header-only fast-path optimization (next step)
grpc-go deserializes each `Frame` by default. For max throughput on 1MB vision frames, register
a custom `encoding.Codec` that decodes only the routing header and forwards the opaque tail as
`[]byte` (zero-copy proxy) — the envelope's low field numbers (1-4) make this a cheap partial
parse. A golden-wire conformance suite (same bytes in/out as the Python impl) guards parity.

## Routing source
`DefaultRouteTable()` is a static bootstrap. Production replaces it with a Postgres
`LISTEN routing_changed` cache mirroring `agentctl/gateway/pg_route_cache.py` (NOTIFY fast-path
+ version-poll backstop), so a Vertical-C rollback flips Go routing with zero dropped streams.
