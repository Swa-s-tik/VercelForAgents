package gateway

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync/atomic"
	"time"

	"github.com/lib/pq"
)

// Resolver is what the proxy needs from a routing table: pick a sticky primary arm + shadows.
// Both the static RouteTable and the Postgres-backed PgRouteTable satisfy it.
type Resolver interface {
	Resolve(sessionID string) (Backend, []Backend)
}

const liveRoutingQuery = `
SELECT rr.deployment_id, rr.weight, rr.is_canary, rr.shadow_target,
       rt.version, d.git_commit_sha, COALESCE(d.build_meta::text, '{}')
FROM controlplane.routing_tables rt
JOIN controlplane.routing_rules rr ON rr.routing_table_id = rt.id
JOIN controlplane.deployments d    ON d.id = rr.deployment_id
WHERE rt.project_id = $1 AND rt.is_live
ORDER BY rr.weight DESC`

// PgRouteTable mirrors agentctl/gateway/pg_route_cache.py: load the live routing from Postgres,
// LISTEN routing_changed (Vertical C's flip fires pg_notify) + a version-poll backstop, and
// atomically swap the in-memory table. Sticky sessions keep active streams alive across a flip.
type PgRouteTable struct {
	dsn       string
	projectID string
	db        *sql.DB
	current   atomic.Pointer[RouteTable]
	reloads   atomic.Uint64
}

func normalizeDSN(dsn string) string {
	d := strings.Replace(dsn, "postgresql://", "postgres://", 1)
	if !strings.Contains(d, "sslmode=") {
		if strings.Contains(d, "?") {
			d += "&sslmode=disable"
		} else {
			d += "?sslmode=disable"
		}
	}
	return d
}

func NewPgRouteTable(dsn, projectID string) (*PgRouteTable, error) {
	nd := normalizeDSN(dsn)
	db, err := sql.Open("postgres", nd)
	if err != nil {
		return nil, err
	}
	p := &PgRouteTable{dsn: nd, projectID: projectID, db: db}
	if err := p.load(); err != nil {
		return nil, err
	}
	return p, nil
}

func (p *PgRouteTable) load() error {
	rows, err := p.db.Query(liveRoutingQuery, p.projectID)
	if err != nil {
		return err
	}
	defer rows.Close()
	var primary, shadow []Backend
	var version uint64
	for rows.Next() {
		var depID, ver, weight int64
		var isCanary, isShadow bool
		var sha, bmeta string
		if err := rows.Scan(&depID, &weight, &isCanary, &isShadow, &ver, &sha, &bmeta); err != nil {
			return err
		}
		version = uint64(ver)
		var bm struct {
			Endpoint   string `json:"endpoint"`
			VersionTag string `json:"version_tag"`
		}
		_ = json.Unmarshal([]byte(bmeta), &bm)
		endpoint := bm.Endpoint
		if endpoint == "" {
			endpoint = "localhost:50051"
		}
		tag := bm.VersionTag
		if tag == "" && len(sha) >= 6 {
			tag = sha[:6]
		}
		b := Backend{ID: fmt.Sprintf("%d", depID), Endpoint: endpoint,
			VersionTag: tag, Weight: uint32(weight), IsCanary: isCanary}
		if isShadow {
			shadow = append(shadow, b)
		} else {
			primary = append(primary, b)
		}
	}
	p.current.Store(&RouteTable{Version: version, Primary: primary, Shadow: shadow})
	p.reloads.Add(1)
	return rows.Err()
}

func (p *PgRouteTable) Resolve(session string) (Backend, []Backend) {
	return p.current.Load().Resolve(session)
}

func (p *PgRouteTable) Version() uint64 { return p.current.Load().Version }
func (p *PgRouteTable) Reloads() uint64 { return p.reloads.Load() }

// Watch starts the LISTEN/NOTIFY worker + version-poll backstop (non-blocking).
func (p *PgRouteTable) Watch() {
	listener := pq.NewListener(p.dsn, time.Second, time.Minute, func(_ pq.ListenerEventType, err error) {
		if err != nil {
			log.Printf("pg listener event: %v", err)
		}
	})
	if err := listener.Listen("routing_changed"); err != nil {
		log.Printf("LISTEN routing_changed failed: %v", err)
	}
	ticker := time.NewTicker(500 * time.Millisecond)
	go func() {
		last := p.Version()
		for {
			select {
			case <-listener.Notify:
				if err := p.load(); err != nil {
					log.Printf("route reload (notify): %v", err)
				} else {
					last = p.Version()
				}
			case <-ticker.C:
				var v int64
				if err := p.db.QueryRow(
					"SELECT COALESCE(max(version),0) FROM controlplane.routing_tables WHERE project_id=$1 AND is_live",
					p.projectID).Scan(&v); err == nil && uint64(v) != last {
					if err := p.load(); err == nil {
						last = p.Version()
					}
				}
			}
		}
	}()
}
