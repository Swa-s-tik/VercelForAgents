# Design - Header-only zero-copy frame forwarding on the Go data plane (post-1.0)

**Status:** done · **Commit:** `feat(gateway): header-only zero-copy frame forwarding (opt-in)`

## Why

The 1.0 status matrix listed "header-only zero-copy forwarding" as the one explicit **Not yet**:
both runtimes fully deserialize *every* `Frame` on the hot path. The Go gateway, on each frame,
ran `Unmarshal` (build the typed `Frame`: oneof payload + nested submessage + the attributes map)
and `Marshal` again to forward it - pure overhead for a proxy that mostly moves bytes through
unchanged. This closes that item: an opt-in fast path that routes and tags frames by touching the
protobuf wire bytes directly, never building the typed struct.

## The two facts that make it safe

Reading `proxy.go`, the gateway only ever touches **two** fields of a Frame:

1. **Routing reads `session_id` (field 1)** - and only on the *first* frame of a stream, to pick the
   sticky canary arm. Every later frame is forwarded verbatim.
2. **Tagging writes `attributes["canary_arm"]` (the map at field 16)** on the return path, so the
   client/telemetry can see which arm served the stream.

Everything else is opaque pass-through. So the hot path never needs the typed message:

- `wire.SessionID(b)` scans the wire bytes for field 1 and stops (it is the lowest field number, so
  on a well-formed frame it is the first tag - one field consumed).
- `wire.SetCanaryArm(b, arm)` **appends** a single field-16 map entry to the tail. This is
  decoder-safe: protobuf merges repeated occurrences of a map field, and on a duplicate key the last
  occurrence wins - so appending overrides any `canary_arm` the backend already set, identical to the
  typed `out.Attributes["canary_arm"] = arm`. The frozen routing/identity header (fields 1-4) is
  never rewritten, so the wire contract the conformance suite pins is preserved byte-for-byte.

## What it does

- `internal/wire/wire.go` - the primitives (`SessionID`, `SetCanaryArm`) + `RawFrame{B []byte}`, the
  opaque message the proxy hands to grpc's `RecvMsg`/`SendMsg`.
- `internal/gateway/rawcodec.go` - `rawCodec` is a drop-in replacement for grpc's `"proto"` codec
  that passes a `*wire.RawFrame` through as raw bytes and **delegates every other type to protobuf**
  (so Health and any typed RPC behave exactly as before). grpc v1.64 selects a codec by
  content-subtype (`"proto"`), so overriding that name swaps behavior process-wide without changing
  call sites. Installed only by `EnableZeroCopy()`.
- `internal/gateway/proxy_raw.go` - `ConverseRaw` is the byte-for-byte twin of `Converse`: same
  sticky-arm resolution, same lossless-primary / bounded drop-on-full-shadow fan-out (the
  `shadowPipe` is now generic over the frame type, so both paths share one implementation), but it
  reads only `session_id` to route and appends `canary_arm` to forward - no per-frame
  `Unmarshal`/`Marshal`. `RawServiceDesc` registers this handler for `Converse` and keeps `Health`
  typed.
- `cmd/gateway/main.go` - when `AGENTCTL_ZEROCOPY=1`, calls `EnableZeroCopy()` and registers the raw
  ServiceDesc; otherwise the generated typed server, **unchanged**.

## Opt-in, default untouched

Like every post-1.0 capability, this is gated: `AGENTCTL_ZEROCOPY=1`. Unset, the data plane is the
exact typed path 1.0 shipped (the passthrough codec is not even registered). So a plain run is
byte-for-byte what it was.

## Measured (`go test -bench Forward|Route -benchmem`, representative text frame)

| Operation | Full deserialize | Zero-copy | Win |
|---|---|---|---|
| Outbound forward (Unmarshal + set canary_arm + Marshal) | 1611 ns/op, 22 allocs | 190 ns/op, 5 allocs | **8.5x faster, 4.4x fewer allocs** |
| Inbound route (read session_id) | 683 ns/op, 13 allocs | 22.6 ns/op, 1 alloc | **30x faster, 13x fewer allocs** |

## Why it stays correct

- `internal/wire/wire_test.go` - `SetCanaryArm` is `proto.Equal` to the typed set; append overrides a
  pre-existing key; the frozen header is unchanged; malformed/absent `session_id` is reported, not
  guessed.
- `internal/gateway/wire_fastpath_test.go` - runs both primitives against **every golden fixture the
  cross-runtime conformance suite uses**: `SessionID` equals the typed `session_id` and
  `Unmarshal(SetCanaryArm(raw))` equals the typed-set Frame, for all frame shapes (text, tool_call,
  binary, control, turn_end, multi-key attributes...). A drift in either primitive fails here.
- `make conformance` is unaffected (the golden-wire suite marshals typed messages directly).
- End-to-end: `agentctl push` streams its ②′ proof through an `AGENTCTL_ZEROCOPY=1` gateway - all 21
  TextDelta frames + the intercepted `issue_refund` tool call arrive intact (proving frame integrity
  through `SetCanaryArm`), and `tests/test_go_gateway_auth.py` passes on both paths.

## Boundary (honest)

The fast path still **copies** the frame bytes once on receive (grpc recycles its read buffer, and
the proxy holds frames across goroutine hops for the shadow lanes). "Zero-copy" here means *no
serialization* - no typed allocation, no `Marshal`/`Unmarshal` - not a literally zero-`memcpy` path.
A true splice (forward the transport buffer without any copy) would need deeper grpc-transport hooks
and is out of scope.
