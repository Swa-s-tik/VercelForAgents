package wire

import (
	"testing"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

// marshal a representative Frame: header + a text payload + a pre-existing attribute, the shape the
// proxy forwards on the hot path.
func sampleFrame(t *testing.T) ([]byte, *acpv1.Frame) {
	t.Helper()
	f := &acpv1.Frame{
		SessionId:   "sess-abc-123",
		StreamId:    7,
		Seq:         42,
		Direction:   acpv1.Direction_AGENT_TO_CLIENT,
		TsUnixNanos: 1<<53 + 1,
		Attributes:  map[string]string{"traceparent": "00-aaaa-bbbb-01"},
		Payload:     &acpv1.Frame_Text{Text: &acpv1.TextDelta{Content: "hello world", Partial: true}},
	}
	b, err := proto.Marshal(f)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return b, f
}

func TestSessionID(t *testing.T) {
	b, f := sampleFrame(t)
	got, ok := SessionID(b)
	if !ok || got != f.SessionId {
		t.Fatalf("SessionID = %q,%v; want %q,true", got, ok, f.SessionId)
	}
}

func TestSessionIDEmptyAndMalformed(t *testing.T) {
	if _, ok := SessionID(nil); ok {
		t.Fatal("SessionID(nil) should be false")
	}
	if _, ok := SessionID([]byte{0xff, 0xff}); ok {
		t.Fatal("SessionID(garbage) should be false")
	}
	// a frame with no session_id set (field 1 absent) must report absent, not ""-present.
	b, _ := proto.Marshal(&acpv1.Frame{Seq: 1})
	if _, ok := SessionID(b); ok {
		t.Fatal("SessionID should be false when field 1 is unset")
	}
}

// SetCanaryArm must be observationally identical to the typed slow path:
// Unmarshal(SetCanaryArm(b, arm)) == the original Frame with Attributes[canary_arm]=arm.
func TestSetCanaryArmEquivalentToTypedSet(t *testing.T) {
	b, f := sampleFrame(t)

	got := &acpv1.Frame{}
	if err := proto.Unmarshal(SetCanaryArm(b, "v37"), got); err != nil {
		t.Fatalf("unmarshal zero-copy: %v", err)
	}

	want := proto.Clone(f).(*acpv1.Frame)
	if want.Attributes == nil {
		want.Attributes = map[string]string{}
	}
	want.Attributes[CanaryArmKey] = "v37"

	if !proto.Equal(got, want) {
		t.Fatalf("zero-copy set != typed set\n got=%v\nwant=%v", got, want)
	}
}

// Appending must win over a canary_arm the backend already set (last-occurrence-wins on the wire),
// and must not disturb the frozen header.
func TestSetCanaryArmOverridesExisting(t *testing.T) {
	f := &acpv1.Frame{
		SessionId:  "s",
		Seq:        9,
		Attributes: map[string]string{CanaryArmKey: "stale", "k": "v"},
		Payload:    &acpv1.Frame_Text{Text: &acpv1.TextDelta{Content: "x"}},
	}
	b, _ := proto.Marshal(f)

	got := &acpv1.Frame{}
	if err := proto.Unmarshal(SetCanaryArm(b, "fresh"), got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.Attributes[CanaryArmKey] != "fresh" {
		t.Fatalf("canary_arm = %q; want fresh (append must override)", got.Attributes[CanaryArmKey])
	}
	if got.Attributes["k"] != "v" || got.SessionId != "s" || got.Seq != 9 {
		t.Fatalf("append disturbed other fields: %+v", got)
	}
	if sid, _ := SessionID(SetCanaryArm(b, "fresh")); sid != "s" {
		t.Fatalf("header session_id changed after append: %q", sid)
	}
}
