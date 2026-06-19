package gateway

import (
	"testing"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
	"github.com/agentctl/gateway_core/internal/wire"
)

// loadGoldenFrames returns each conformance fixture as (raw wire bytes, typed Frame) - the exact
// golden bytes the cross-runtime conformance suite pins.
func loadGoldenFrames(t testing.TB) [][2]any {
	specs, err := LoadSpecs(DefaultFixturePath())
	if err != nil {
		t.Fatalf("load fixtures: %v", err)
	}
	out := make([][2]any, 0, len(specs))
	for _, s := range specs {
		f, err := BuildFrame(s)
		if err != nil {
			t.Fatalf("build %q: %v", s.Name, err)
		}
		raw, err := proto.Marshal(f)
		if err != nil {
			t.Fatalf("marshal %q: %v", s.Name, err)
		}
		out = append(out, [2]any{raw, f})
	}
	return out
}

// The fast path must agree with the typed path on every golden frame:
//   - wire.SessionID == Frame.session_id (routing read)
//   - Unmarshal(wire.SetCanaryArm(raw, arm)) == typed Frame with attributes[canary_arm]=arm
//
// This pins the zero-copy path to the SAME fixtures the byte-for-byte conformance suite uses, so a
// drift in either the header scan or the attribute append fails here.
func TestWireFastPathMatchesFixtures(t *testing.T) {
	const arm = "v37-canary"
	for _, fr := range loadGoldenFrames(t) {
		raw := fr[0].([]byte)
		f := fr[1].(*acpv1.Frame)

		if sid, ok := wire.SessionID(raw); !ok || sid != f.SessionId {
			t.Fatalf("SessionID = %q,%v; want %q", sid, ok, f.SessionId)
		}

		got := &acpv1.Frame{}
		if err := proto.Unmarshal(wire.SetCanaryArm(raw, arm), got); err != nil {
			t.Fatalf("unmarshal zero-copy: %v", err)
		}
		want := proto.Clone(f).(*acpv1.Frame)
		if want.Attributes == nil {
			want.Attributes = map[string]string{}
		}
		want.Attributes[wire.CanaryArmKey] = arm
		if !proto.Equal(got, want) {
			t.Fatalf("zero-copy != typed for session %q\n got=%v\nwant=%v", f.SessionId, got, want)
		}
	}
}

// benchFrame is the representative hot-path frame: a downstream text delta the size of a real
// token chunk, carrying one inbound attribute.
func benchFrame() []byte {
	f := &acpv1.Frame{
		SessionId:   "sess-7f3a9c2e-streaming-conversation",
		StreamId:    3,
		Seq:         128,
		Direction:   acpv1.Direction_AGENT_TO_CLIENT,
		TsUnixNanos: 1_700_000_000_000_000_000,
		Attributes:  map[string]string{"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
		Payload: &acpv1.Frame_Text{Text: &acpv1.TextDelta{
			Content: "The refund has been processed and a confirmation email is on its way.", Partial: true}},
	}
	b, _ := proto.Marshal(f)
	return b
}

// BenchmarkForwardOutbound_FullDeserialize is the slow path proxy.go runs today on every outbound
// frame: Unmarshal -> set attributes[canary_arm] -> Marshal.
func BenchmarkForwardOutbound_FullDeserialize(b *testing.B) {
	raw := benchFrame()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		f := &acpv1.Frame{}
		if err := proto.Unmarshal(raw, f); err != nil {
			b.Fatal(err)
		}
		if f.Attributes == nil {
			f.Attributes = map[string]string{}
		}
		f.Attributes["canary_arm"] = "v37"
		if _, err := proto.Marshal(f); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkForwardOutbound_ZeroCopy is the fast path: append one map entry to the tail.
func BenchmarkForwardOutbound_ZeroCopy(b *testing.B) {
	raw := benchFrame()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = wire.SetCanaryArm(raw, "v37")
	}
}

// BenchmarkRouteInbound_FullDeserialize is the slow path for reading the routing key: Unmarshal the
// whole frame just to read session_id.
func BenchmarkRouteInbound_FullDeserialize(b *testing.B) {
	raw := benchFrame()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		f := &acpv1.Frame{}
		if err := proto.Unmarshal(raw, f); err != nil {
			b.Fatal(err)
		}
		_ = f.SessionId
	}
}

// BenchmarkRouteInbound_ZeroCopy scans field 1 only.
func BenchmarkRouteInbound_ZeroCopy(b *testing.B) {
	raw := benchFrame()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = wire.SessionID(raw)
	}
}
