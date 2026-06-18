package gateway

import (
	"encoding/hex"
	"testing"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

// TestConformance proves the Go data plane is wire-compatible with the Python reference proxy on
// the frozen Frame envelope. Protobuf deterministic marshaling is per-runtime canonical (Go orders
// the oneof after the map, Python before it), so the contract is NOT byte-identical full frames —
// it is (1) a byte-identical frozen header (the header-only forwarding contract) and (2) lossless
// cross-runtime decode. Both are asserted here against goldens produced by Python.
func TestConformance(t *testing.T) {
	specs, err := LoadSpecs(DefaultFixturePath())
	if err != nil {
		t.Fatalf("load specs: %v", err)
	}
	if len(specs) == 0 {
		t.Fatal("no specs loaded")
	}
	for _, s := range specs {
		s := s
		t.Run(s.Name, func(t *testing.T) {
			// (1) the frozen header marshals to exactly the bytes Python produces.
			hh, err := MarshalHex(HeaderFrame(s))
			if err != nil {
				t.Fatalf("header marshal: %v", err)
			}
			if hh != s.HeaderHex {
				t.Fatalf("frozen-header byte drift\n  go = %s\n  py = %s", hh, s.HeaderHex)
			}

			// (2) Go losslessly decodes Python's wire into the SAME logical frame Go would build
			// from the spec (compared via Go's own canonical marshal — order-independent).
			raw, err := hex.DecodeString(s.GoldenHex)
			if err != nil {
				t.Fatalf("bad golden hex: %v", err)
			}
			var decoded acpv1.Frame
			if err := proto.Unmarshal(raw, &decoded); err != nil {
				t.Fatalf("Go cannot decode Python wire: %v", err)
			}
			fromPython, err := MarshalHex(&decoded)
			if err != nil {
				t.Fatal(err)
			}
			built, err := BuildFrame(s)
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			fromSpec, err := MarshalHex(built)
			if err != nil {
				t.Fatal(err)
			}
			if fromPython != fromSpec {
				t.Fatalf("Go decode of Python wire is not the expected frame\n  decoded = %s\n  built   = %s",
					fromPython, fromSpec)
			}
		})
	}
}
