// Control-plane / Health message conformance builder (extends the golden-wire suite beyond Frame).
// Mirrors tests/conformance_control.py::build_control with identical field values, so the same
// fixtures (tests/fixtures/conformance_control.json) verify Go<->Python decode interop for the
// ControlPlane + Health messages.
package gateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

type ControlSpec struct {
	Name      string `json:"name"`
	GoldenHex string `json:"golden_hex"`
}

func shadowPolicy() *acpv1.ShadowPolicy {
	return &acpv1.ShadowPolicy{MockSideEffects: true, SamplePercent: 10, MaxAddedLatencyMs: 50}
}

func backend(id, ep string, w uint32, tag string, bin bool) *acpv1.Backend {
	return &acpv1.Backend{BackendId: id, Endpoint: ep, Weight: w, VersionTag: tag,
		AcceptsBinary: bin, ShadowPolicy: shadowPolicy()}
}

func telemetryEvent(sid, arm string) *acpv1.TelemetryEvent {
	return &acpv1.TelemetryEvent{SessionId: sid, DeploymentId: "dep-7", CanaryArm: arm,
		EventType: "stream", Measures: map[string]float64{"latency_ms": 33.5, "frames": 21.0},
		Labels: map[string]string{"region": "us"}, TsUnixNanos: 1700000000000000001}
}

// BuildControl mirrors the Python builder for one named control/health message.
func BuildControl(name string) (proto.Message, error) {
	switch name {
	case "ResolveRouteRequest":
		return &acpv1.ResolveRouteRequest{DeploymentId: "dep-7"}, nil
	case "WatchRequest":
		return &acpv1.WatchRequest{DeploymentIds: []string{"dep-7", "dep-8"}}, nil
	case "ShadowPolicy":
		return shadowPolicy(), nil
	case "Backend":
		return backend("b-1", "localhost:50051", 9000, "vA", true), nil
	case "RouteTable":
		return &acpv1.RouteTable{DeploymentId: "dep-7", Version: 42,
			Primary: []*acpv1.Backend{backend("b-1", "localhost:50051", 9000, "vA", true),
				backend("b-2", "localhost:50052", 1000, "vB", false)},
			Shadow:     []*acpv1.Backend{backend("s-1", "localhost:50053", 0, "shadow", true)},
			Sticky:     acpv1.StickyPolicy_STICKY_SESSION, TtlSeconds: 300}, nil
	case "TelemetryEvent":
		return telemetryEvent("s1", "vA"), nil
	case "TelemetryBatch":
		return &acpv1.TelemetryBatch{Events: []*acpv1.TelemetryEvent{
			telemetryEvent("s1", "vA"), telemetryEvent("s2", "vB")}}, nil
	case "TelemetryAck":
		return &acpv1.TelemetryAck{Accepted: 2}, nil
	case "HealthRequest":
		return &acpv1.HealthRequest{DeploymentId: "dep-7"}, nil
	case "HealthReply":
		return &acpv1.HealthReply{Ready: true, InflightStreams: 3, MaxStreams: 100,
			SupportedModalities: []acpv1.Modality{acpv1.Modality_TEXT, acpv1.Modality_IMAGE},
			VersionTag: "vA"}, nil
	}
	return nil, fmt.Errorf("unknown control message: %q", name)
}

func ControlFixturePath() string {
	_, thisFile, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(thisFile), "..", "..", "..", "..")
	return filepath.Join(root, "tests", "fixtures", "conformance_control.json")
}

func LoadControlSpecs(path string) ([]ControlSpec, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc struct {
		Messages []ControlSpec `json:"messages"`
	}
	if err := json.NewDecoder(bytes.NewReader(raw)).Decode(&doc); err != nil {
		return nil, err
	}
	return doc.Messages, nil
}
