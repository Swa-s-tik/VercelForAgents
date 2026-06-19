// Package wire is the header-only fast path for the Go data plane: it lets the gateway route
// and tag Frames by touching the protobuf wire bytes directly, without deserializing the whole
// message (the oneof payload + nested submessages + the attributes map).
//
// It exists because of two facts about how the proxy actually uses a Frame (see proxy.go):
//
//   - routing reads exactly one field, session_id (field 1), and only on the first frame;
//   - the gateway writes exactly one field, attributes["canary_arm"] (the map at field 16), on
//     outbound frames.
//
// Every other frame is forwarded verbatim. So the hot path never needs the typed struct:
// SessionID scans field 1 and stops; SetCanaryArm appends one map entry to the tail. The frozen
// routing/identity header (fields 1-4) is never rewritten, so the wire contract the conformance
// suite pins is preserved byte-for-byte (proven in wire_test.go against the same fixtures).
package wire

import "google.golang.org/protobuf/encoding/protowire"

// RawFrame is an opaque, already-serialized Frame. It is the message type the zero-copy proxy
// path passes to grpc's RecvMsg/SendMsg so the transport hands over the wire bytes verbatim
// (paired with the passthrough Codec in the gateway package) instead of building a typed Frame.
type RawFrame struct{ B []byte }

// Frame field numbers (must match proto/agent_control.proto - the frozen envelope).
const (
	fieldSessionID  = 1  // string  (frozen header)
	fieldAttributes = 16 // map<string,string>
)

// CanaryArmKey is the attributes key the gateway stamps on outbound frames so the client/telemetry
// can see which canary arm served the stream. Must match agentctl/gateway/proxy.py + proxy.go.
const CanaryArmKey = "canary_arm"

// SessionID extracts Frame.session_id (field 1) by scanning the wire bytes, without building the
// Frame struct. Returns ("", false) if the field is absent or the bytes are malformed. session_id
// is the lowest field number, so on a well-formed frame it is the first tag and the scan returns
// after one field.
func SessionID(b []byte) (string, bool) {
	for len(b) > 0 {
		num, typ, n := protowire.ConsumeTag(b)
		if n < 0 {
			return "", false
		}
		b = b[n:]
		if num == fieldSessionID && typ == protowire.BytesType {
			v, n := protowire.ConsumeBytes(b)
			if n < 0 {
				return "", false
			}
			return string(v), true
		}
		// Not our field: skip its value and continue. Fields above session_id are rare on the
		// hot path (session_id is serialized first), so this loop usually runs once.
		n = protowire.ConsumeFieldValue(num, typ, b)
		if n < 0 {
			return "", false
		}
		b = b[n:]
	}
	return "", false
}

// SetCanaryArm returns b with attributes["canary_arm"]=arm injected, by appending a single
// map-entry occurrence of field 16 to the tail. This is wire-legal and decoder-safe: protobuf
// merges repeated occurrences of a map field, and on a duplicate key the last occurrence wins, so
// appending overrides any canary_arm the backend already set - identical to the typed
// out.Attributes["canary_arm"]=arm the slow path does. The frozen header (fields 1-4) is untouched.
//
// The input b is not mutated; the result is a fresh slice.
func SetCanaryArm(b []byte, arm string) []byte {
	entry := appendMapEntry(nil, CanaryArmKey, arm)
	out := make([]byte, 0, len(b)+len(entry)+2)
	out = append(out, b...)
	out = protowire.AppendTag(out, fieldAttributes, protowire.BytesType)
	out = protowire.AppendBytes(out, entry)
	return out
}

// appendMapEntry encodes a map<string,string> entry submessage {1:key, 2:value} onto dst.
func appendMapEntry(dst []byte, key, val string) []byte {
	dst = protowire.AppendTag(dst, 1, protowire.BytesType)
	dst = protowire.AppendString(dst, key)
	dst = protowire.AppendTag(dst, 2, protowire.BytesType)
	dst = protowire.AppendString(dst, val)
	return dst
}
