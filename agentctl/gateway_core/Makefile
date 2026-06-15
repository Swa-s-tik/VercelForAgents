# Codegen + build for the Go data-plane gateway. Requires: go >= 1.22, protoc,
# protoc-gen-go, protoc-gen-go-grpc on PATH. Protos are the SINGLE SOURCE OF TRUTH at
# ../../proto (the same frozen contract the Python proxy honors).
PROTO_SRC := ../../proto
PKG       := github.com/agentctl/gateway_core/gen/acpv1
MMAP      := Menvelope.proto=$(PKG),Mdataplane.proto=$(PKG),Mcontrolplane.proto=$(PKG)

.PHONY: proto build run tidy clean

proto:
	mkdir -p gen/acpv1
	protoc -I $(PROTO_SRC) \
	  --go_out=gen/acpv1      --go_opt=paths=source_relative,$(MMAP) \
	  --go-grpc_out=gen/acpv1 --go-grpc_opt=paths=source_relative,$(MMAP) \
	  $(PROTO_SRC)/envelope.proto $(PROTO_SRC)/dataplane.proto $(PROTO_SRC)/controlplane.proto

tidy:
	go mod tidy

build: proto tidy
	go build -o bin/gateway ./cmd/gateway

run: build
	./bin/gateway

clean:
	rm -rf gen bin
