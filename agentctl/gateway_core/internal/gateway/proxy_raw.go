package gateway

import (
	"context"
	"io"

	"google.golang.org/grpc"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
	"github.com/agentctl/gateway_core/internal/wire"
)

// converseStreamDesc describes the bidi Converse method for raw client legs (NewStream needs it).
var converseStreamDesc = &grpc.StreamDesc{
	StreamName:    "Converse",
	ServerStreams: true,
	ClientStreams: true,
}

// rawConverseClient opens a bidi Converse stream to a backend that carries opaque *wire.RawFrame
// messages (the passthrough codec forwards the bytes). Reuses the pooled connection.
func (s *Server) rawConverseClient(ctx context.Context, endpoint string) (grpc.ClientStream, error) {
	cc, err := s.conn(endpoint)
	if err != nil {
		return nil, err
	}
	return cc.NewStream(ctx, converseStreamDesc, "/acp.v1.AgentStream/Converse")
}

// ConverseRaw is the zero-copy twin of Converse: byte-for-byte the same routing/shadow/canary-tag
// behavior, but it never builds a typed Frame. It reads only session_id (field 1) from the first
// frame to pick the sticky arm, forwards every frame as opaque bytes to primary + shadows, and on
// the return path appends attributes["canary_arm"] (field 16) instead of unmarshal-mutate-marshal.
// Registered (in place of the generated typed handler) only when AGENTCTL_ZEROCOPY=1.
func (s *Server) ConverseRaw(stream grpc.ServerStream) error {
	var first wire.RawFrame
	if err := stream.RecvMsg(&first); err != nil {
		return err
	}
	sid, _ := wire.SessionID(first.B)
	primary, shadows := s.resolver.Resolve(sid)

	up, err := s.rawConverseClient(stream.Context(), primary.Endpoint)
	if err != nil {
		return err
	}

	// shadow lanes: same bounded, drop-on-full discipline as the typed path (shadow.go), so a slow
	// shadow can never backpressure the primary. Responses are drained and discarded.
	var shadowPipes []*shadowPipe[*wire.RawFrame]
	for _, sb := range shadows {
		sc, err := s.rawConverseClient(context.Background(), sb.Endpoint)
		if err != nil {
			continue
		}
		shadowPipes = append(shadowPipes, newShadowPipe(func(f *wire.RawFrame) error { return sc.SendMsg(f) }))
		go func() { // drain + discard shadow responses
			for {
				if err := sc.RecvMsg(&wire.RawFrame{}); err != nil {
					return
				}
			}
		}()
	}

	// pump: client -> primary (lossless, blocking) + shadows (lossy, non-blocking offer).
	go func() {
		_ = up.SendMsg(&first)
		for _, sp := range shadowPipes {
			sp.offer(&first)
		}
		for {
			var in wire.RawFrame
			if err := stream.RecvMsg(&in); err != nil {
				_ = up.CloseSend()
				for _, sp := range shadowPipes {
					sp.close()
				}
				return
			}
			f := in // capture: in is reused next iteration
			_ = up.SendMsg(&f)
			for _, sp := range shadowPipes {
				sp.offer(&f)
			}
		}
	}()

	// primary -> client: append the canary_arm tag to the wire bytes, no full (de)serialize.
	for {
		var out wire.RawFrame
		if err := up.RecvMsg(&out); err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		out.B = wire.SetCanaryArm(out.B, primary.VersionTag)
		if err := stream.SendMsg(&out); err != nil {
			return err
		}
	}
}

// RawServiceDesc registers the AgentStream service with the zero-copy Converse handler in place of
// the generated typed one; Health stays typed (its messages go through the codec's proto fallback).
func RawServiceDesc(s *Server) grpc.ServiceDesc {
	return grpc.ServiceDesc{
		ServiceName: "acp.v1.AgentStream",
		HandlerType: (*any)(nil),
		Methods: []grpc.MethodDesc{
			{
				MethodName: "Health",
				Handler: func(srv any, ctx context.Context, dec func(any) error, interceptor grpc.UnaryServerInterceptor) (any, error) {
					in := new(acpv1.HealthRequest)
					if err := dec(in); err != nil {
						return nil, err
					}
					h := srv.(*Server).Health
					if interceptor == nil {
						return h(ctx, in)
					}
					info := &grpc.UnaryServerInfo{Server: srv, FullMethod: "/acp.v1.AgentStream/Health"}
					return interceptor(ctx, in, info, func(ctx context.Context, req any) (any, error) {
						return h(ctx, req.(*acpv1.HealthRequest))
					})
				},
			},
		},
		Streams: []grpc.StreamDesc{
			{
				StreamName: "Converse",
				Handler: func(srv any, stream grpc.ServerStream) error {
					return srv.(*Server).ConverseRaw(stream)
				},
				ServerStreams: true,
				ClientStreams: true,
			},
		},
	}
}
