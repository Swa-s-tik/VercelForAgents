// API-key auth for the Go data plane (Workstream 2). The Go gateway's job is routing; the control
// plane / SoR is the authority on key validity. So these interceptors are WIRED BUT PERMISSIVE by
// default — they pass every call through. Set AGENTCTL_REQUIRE_KEY=1 to require an `x-api-key`
// metadata value to be present (a presence check; full validation against Postgres is a documented
// post-1.0 item). The Python reference proxy enforces full validation today.
package gateway

import (
	"context"
	"os"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func requireKey() bool { return os.Getenv("AGENTCTL_REQUIRE_KEY") == "1" }

func apiKeyFrom(ctx context.Context) string {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return ""
	}
	if v := md.Get("x-api-key"); len(v) > 0 {
		return v[0]
	}
	return ""
}

// StreamAuthInterceptor guards streaming RPCs (Converse).
func StreamAuthInterceptor(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo,
	handler grpc.StreamHandler) error {
	if requireKey() && apiKeyFrom(ss.Context()) == "" {
		return status.Error(codes.Unauthenticated, "API key required (AGENTCTL_REQUIRE_KEY=1)")
	}
	return handler(srv, ss)
}

// UnaryAuthInterceptor guards unary RPCs (Health).
func UnaryAuthInterceptor(ctx context.Context, req any, info *grpc.UnaryServerInfo,
	handler grpc.UnaryHandler) (any, error) {
	if requireKey() && apiKeyFrom(ctx) == "" {
		return nil, status.Error(codes.Unauthenticated, "API key required (AGENTCTL_REQUIRE_KEY=1)")
	}
	return handler(ctx, req)
}
