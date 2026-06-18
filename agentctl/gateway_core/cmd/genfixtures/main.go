// genfixtures: verify the Go runtime is wire-compatible with the Python goldens, and emit Go's
// own deterministic wire to tests/fixtures/conformance_go_wire.json so the Python suite can prove
// the reverse direction (Python decodes Go's wire). Run from gateway_core: `make fixtures`.
//
// It checks, per frame: (1) the frozen header marshals byte-identically to Python's header_hex,
// (2) Go decodes Python's golden into the same logical frame Go builds from the spec. Exits
// non-zero on any drift. (Full-frame bytes intentionally differ from Python — protobuf
// deterministic marshaling is per-runtime canonical, not cross-runtime.)
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"google.golang.org/protobuf/proto"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
	gw "github.com/agentctl/gateway_core/internal/gateway"
)

func main() {
	path := gw.DefaultFixturePath()
	specs, err := gw.LoadSpecs(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "load specs: %v\n", err)
		os.Exit(1)
	}
	fail := 0
	goWire := map[string]string{}
	for _, s := range specs {
		built, err := gw.BuildFrame(s)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: build: %v\n", s.Name, err)
			os.Exit(1)
		}
		goHex, _ := gw.MarshalHex(built)
		goWire[s.Name] = goHex

		// header byte-identity
		hh, _ := gw.MarshalHex(gw.HeaderFrame(s))
		headerOK := hh == s.HeaderHex

		// decode interop: Go reads Python's golden into the same logical frame
		raw, _ := hex.DecodeString(s.GoldenHex)
		var dec acpv1.Frame
		decodeOK := proto.Unmarshal(raw, &dec) == nil
		if decodeOK {
			fromPy, _ := gw.MarshalHex(&dec)
			decodeOK = fromPy == goHex
		}

		status := "ok"
		if !headerOK || !decodeOK {
			status = "FAIL"
			fail++
		}
		fmt.Printf("%-26s header=%-5v decode=%-5v %s\n", s.Name, headerOK, decodeOK, status)
	}

	// emit Go wire for the Python symmetric check
	outPath := filepath.Join(filepath.Dir(path), "conformance_go_wire.json")
	blob, _ := json.MarshalIndent(map[string]any{
		"_doc":  "Go deterministic proto marshal per frame. tests/test_conformance.py decodes these to prove Python reads Go's wire. Regenerate via `make fixtures`.",
		"frames": goWire,
	}, "", "  ")
	if err := os.WriteFile(outPath, append(blob, '\n'), 0o644); err != nil {
		fmt.Fprintf(os.Stderr, "write go wire: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("\nwrote %s\n", outPath)
	if fail > 0 {
		fmt.Fprintf(os.Stderr, "%d frame(s) failed the Go↔Python wire contract\n", fail)
		os.Exit(1)
	}
	fmt.Printf("all %d frames satisfy the Go↔Python wire contract\n", len(specs))
}
