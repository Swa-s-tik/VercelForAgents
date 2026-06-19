// High-performance data-plane gateway (scaffold) - the eventual lift-and-shift target for
// the Python grpc.aio proxy, behind the SAME frozen proto contract (../../proto).
module github.com/agentctl/gateway_core

go 1.23

require (
	github.com/lib/pq v1.12.3
	google.golang.org/grpc v1.64.0
	google.golang.org/protobuf v1.36.11
)

require (
	golang.org/x/net v0.22.0 // indirect
	golang.org/x/sys v0.18.0 // indirect
	golang.org/x/text v0.14.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240318140521-94a12d6c2237 // indirect
)
