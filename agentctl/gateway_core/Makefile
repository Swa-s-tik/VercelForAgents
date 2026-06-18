# Codegen + build for the Go data-plane gateway. Requires: go >= 1.22, protoc,
# protoc-gen-go, protoc-gen-go-grpc on PATH. Protos are the SINGLE SOURCE OF TRUTH at
# ../../proto (the same frozen contract the Python proxy honors).
PROTO_SRC := ../../proto
PKG       := github.com/agentctl/gateway_core/gen/acpv1
MMAP      := Menvelope.proto=$(PKG),Mdataplane.proto=$(PKG),Mcontrolplane.proto=$(PKG)

.PHONY: proto build run tidy clean fixtures conformance

proto:
	mkdir -p gen/acpv1
	protoc -I $(PROTO_SRC) \
	  --go_out=gen/acpv1      --go_opt=paths=source_relative,$(MMAP) \
	  --go-grpc_out=gen/acpv1 --go-grpc_opt=paths=source_relative,$(MMAP) \
	  $(PROTO_SRC)/envelope.proto $(PROTO_SRC)/dataplane.proto $(PROTO_SRC)/controlplane.proto

tidy:
	go mod tidy

# gen/ is vendored, so `make build` just compiles; run `make proto` to regenerate the stubs.
build:
	go build -trimpath -o bin/gateway ./cmd/gateway

run: build
	./bin/gateway

# Golden-wire conformance (Workstream 4): prove the Go runtime marshals the shared frame specs to
# the same bytes the Python suite (tests/test_conformance.py) asserts.
conformance:
	go test ./internal/gateway/ -run Conformance -v

# Marshal each spec via Go and compare to the committed golden_hex (cross-check / regen aid).
fixtures:
	go run ./cmd/genfixtures

clean:
	rm -rf gen bin
