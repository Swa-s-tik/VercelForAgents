package main

import (
	"log"
	"net"

	"google.golang.org/grpc"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
	"github.com/agentctl/gateway_core/internal/gateway"
)

const mb = 1 << 20

func main() {
	lis, err := net.Listen("tcp", ":50050")
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	srv := grpc.NewServer(
		grpc.MaxRecvMsgSize(64*mb),
		grpc.MaxSendMsgSize(64*mb),
	)
	acpv1.RegisterAgentStreamServer(srv, gateway.NewServer(gateway.DefaultRouteTable()))
	log.Println("gateway_core (Go data plane) listening on :50050")
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("serve: %v", err)
	}
}
