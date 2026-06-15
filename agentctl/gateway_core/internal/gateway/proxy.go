package gateway

import (
	"context"
	"io"
	"sync"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	// Generated from ../../proto by `make proto` (go_package = .../gen/acpv1).
	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

// Server is the streaming reverse proxy — the Go counterpart of agentctl/gateway/proxy.py.
// Routes/splits/mirrors Frames; never parses token text. Designed for a header-only fast
// path: forward the opaque Frame, touching only the routing header (fields 1-4).
type Server struct {
	acpv1.UnimplementedAgentStreamServer
	rt    *RouteTable
	mu    sync.Mutex
	conns map[string]*grpc.ClientConn // one pooled channel per backend endpoint
}

func NewServer(rt *RouteTable) *Server {
	return &Server{rt: rt, conns: map[string]*grpc.ClientConn{}}
}

func (s *Server) client(endpoint string) (acpv1.AgentStreamClient, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cc, ok := s.conns[endpoint]
	if !ok {
		var err error
		cc, err = grpc.NewClient(endpoint,
			grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithDefaultCallOptions(
				grpc.MaxCallRecvMsgSize(64<<20), grpc.MaxCallSendMsgSize(64<<20)))
		if err != nil {
			return nil, err
		}
		s.conns[endpoint] = cc
	}
	return acpv1.NewAgentStreamClient(cc), nil
}

// Converse: the one hot-path RPC. Resolve a sticky arm, open a bidi call to the primary,
// fan inbound frames to primary (lossless) + shadows (lossy), stream primary responses back.
func (s *Server) Converse(stream acpv1.AgentStream_ConverseServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	primary, shadows := s.rt.Resolve(first.GetSessionId())

	pc, err := s.client(primary.Endpoint)
	if err != nil {
		return err
	}
	up, err := pc.Converse(stream.Context())
	if err != nil {
		return err
	}

	// shadow channels (responses discarded; drop on send error — never block the primary)
	var shadowSends []func(*acpv1.Frame)
	for _, sb := range shadows {
		if sc, err := s.client(sb.Endpoint); err == nil {
			if scall, err := sc.Converse(context.Background()); err == nil {
				shadowSends = append(shadowSends, func(f *acpv1.Frame) { _ = scall.Send(f) })
				go func() { // drain + discard
					for {
						if _, err := scall.Recv(); err != nil {
							return
						}
					}
				}()
			}
		}
	}

	// pump: client -> primary (+ shadows), starting with the first frame
	go func() {
		_ = up.Send(first)
		for _, send := range shadowSends {
			send(first)
		}
		for {
			in, err := stream.Recv()
			if err != nil {
				_ = up.CloseSend()
				return
			}
			_ = up.Send(in)
			for _, send := range shadowSends {
				send(in) // lossy fan-out
			}
		}
	}()

	// primary -> client
	for {
		out, err := up.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if out.Attributes == nil {
			out.Attributes = map[string]string{}
		}
		out.Attributes["canary_arm"] = primary.VersionTag
		if err := stream.Send(out); err != nil {
			return err
		}
	}
}

func (s *Server) Health(_ context.Context, _ *acpv1.HealthRequest) (*acpv1.HealthReply, error) {
	return &acpv1.HealthReply{Ready: true, VersionTag: "gateway-core"}, nil
}
