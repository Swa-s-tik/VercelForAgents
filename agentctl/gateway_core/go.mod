// High-performance data-plane gateway (scaffold) — the eventual lift-and-shift target for
// the Python grpc.aio proxy, behind the SAME frozen proto contract (../../proto).
module github.com/agentctl/gateway_core

go 1.22

require (
	google.golang.org/grpc v1.62.0
	google.golang.org/protobuf v1.33.0
)
