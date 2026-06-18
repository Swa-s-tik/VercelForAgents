package main

import (
	"log"
	"net"
	"os"

	"google.golang.org/grpc"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
	"github.com/agentctl/gateway_core/internal/gateway"
)

const mb = 1 << 20

func main() {
	port := os.Getenv("AGENTCTL_GW_PORT")
	if port == "" {
		port = "50050"
	}
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}

	// Cutover: routing comes from Postgres (LISTEN/NOTIFY) when AGENTCTL_PG_DSN is set,
	// so Vertical C's flip transactions update THIS live Go gateway. Falls back to a static
	// table when unset (offline / unit use).
	var resolver gateway.Resolver
	if dsn := os.Getenv("AGENTCTL_PG_DSN"); dsn != "" {
		project := os.Getenv("AGENTCTL_PROJECT_ID")
		pg, err := gateway.NewPgRouteTable(dsn, project)
		if err != nil {
			log.Fatalf("pg route table: %v", err)
		}
		pg.Watch()
		resolver = pg
		log.Printf("routing: Postgres LISTEN/NOTIFY (project=%s)", project)
	} else {
		resolver = gateway.DefaultRouteTable()
		log.Printf("routing: static default table")
	}

	// API-key auth: full validation against Postgres when AGENTCTL_PG_DSN is set (same DSN as
	// routing), else a presence check. Permissive unless AGENTCTL_REQUIRE_KEY=1.
	authn := gateway.NewAuthenticator(os.Getenv("AGENTCTL_PG_DSN"))
	srv := grpc.NewServer(
		grpc.MaxRecvMsgSize(64*mb), grpc.MaxSendMsgSize(64*mb),
		grpc.StreamInterceptor(authn.StreamInterceptor),
		grpc.UnaryInterceptor(authn.UnaryInterceptor),
	)
	acpv1.RegisterAgentStreamServer(srv, gateway.NewServer(resolver))
	log.Printf("gateway_core (Go data plane) listening on :%s", port)
	if err := srv.Serve(lis); err != nil {
		log.Fatalf("serve: %v", err)
	}
}
