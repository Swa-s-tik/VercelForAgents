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

// Server is the streaming reverse proxy - the Go counterpart of agentctl/gateway/proxy.py.
// Routes/splits/mirrors Frames; never parses token text. Designed for a header-only fast
// path: forward the opaque Frame, touching only the routing header (fields 1-4).
type Server struct {
	acpv1.UnimplementedAgentStreamServer
	resolver Resolver
	mu       sync.Mutex
	conns    map[string]*grpc.ClientConn // one pooled channel per backend endpoint
}

func NewServer(r Resolver) *Server {
	return &Server{resolver: r, conns: map[string]*grpc.ClientConn{}}
}

// conn returns a pooled gRPC channel to endpoint (one per backend), shared by the typed and the
// zero-copy raw paths.
func (s *Server) conn(endpoint string) (*grpc.ClientConn, error) {
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
	return cc, nil
}

func (s *Server) client(endpoint string) (acpv1.AgentStreamClient, error) {
	cc, err := s.conn(endpoint)
	if err != nil {
		return nil, err
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
	primary, shadows := s.resolver.Resolve(first.GetSessionId())

	pc, err := s.client(primary.Endpoint)
	if err != nil {
		return err
	}
	up, err := pc.Converse(stream.Context())
	if err != nil {
		return err
	}

	// shadow lanes: each is a BOUNDED, drop-on-full pipe drained by its own goroutine (see
	// shadow.go). Lossy by design, so a slow/stuck shadow backend can never flow-control the
	// primary — offer() in the pump below never blocks. Shadow responses are discarded.
	var shadowPipes []*shadowPipe[*acpv1.Frame]
	for _, sb := range shadows {
		if sc, err := s.client(sb.Endpoint); err == nil {
			if scall, err := sc.Converse(context.Background()); err == nil {
				shadowPipes = append(shadowPipes, newShadowPipe(scall.Send))
				go func() { // drain + discard shadow responses
					for {
						if _, err := scall.Recv(); err != nil {
							return
						}
					}
				}()
			}
		}
	}

	// pump: client -> primary (lossless, blocking) + shadows (lossy, non-blocking offer), starting
	// with the first frame. A slow shadow fills its bounded buffer and drops; the primary is untouched.
	go func() {
		_ = up.Send(first)
		for _, sp := range shadowPipes {
			sp.offer(first)
		}
		for {
			in, err := stream.Recv()
			if err != nil {
				_ = up.CloseSend()
				for _, sp := range shadowPipes {
					sp.close()
				}
				return
			}
			_ = up.Send(in)
			for _, sp := range shadowPipes {
				sp.offer(in) // lossy, never blocks the primary
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
