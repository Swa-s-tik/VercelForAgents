// API-key authentication + RBAC for the Go data plane (post-1.0).
//
// 1.0 only presence-checked the x-api-key metadata. This validates it against
// controlplane.api_keys in Postgres (sha256 hash lookup; revoked keys excluded), enforces the
// key's project matches this gateway's project (tenant isolation) and a minimum role, and caches
// results for a short TTL to keep Postgres off the per-call hot path. With no DSN (offline/static
// routing) it degrades to the prior presence-check, so unit/offline use is unchanged.
//
// Env: AGENTCTL_REQUIRE_KEY=1 makes a key mandatory; AGENTCTL_PROJECT_ID is the tenant a key must
// belong to (empty = any); AGENTCTL_MIN_ROLE is the minimum role (default viewer).
package gateway

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

var roleRank = map[string]int{"viewer": 0, "developer": 1, "admin": 2, "owner": 3}

type principal struct {
	projectID string
	role      string
}

type authConfig struct {
	requireKey bool
	projectID  string
	minRole    string
}

type cacheEntry struct {
	p     *principal
	found bool
	exp   time.Time
}

// Authenticator validates API keys with a short-TTL cache.
type Authenticator struct {
	db    *sql.DB
	cfg   authConfig
	ttl   time.Duration
	mu    sync.RWMutex
	cache map[string]cacheEntry
}

func envDefault(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

// NewAuthenticator reads config from the environment and opens a Postgres pool when dsn != "".
func NewAuthenticator(dsn string) *Authenticator {
	a := &Authenticator{
		cfg: authConfig{
			requireKey: os.Getenv("AGENTCTL_REQUIRE_KEY") == "1",
			projectID:  os.Getenv("AGENTCTL_PROJECT_ID"),
			minRole:    envDefault("AGENTCTL_MIN_ROLE", "viewer"),
		},
		ttl:   15 * time.Second,
		cache: map[string]cacheEntry{},
	}
	if dsn != "" {
		if db, err := sql.Open("postgres", normalizeDSN(dsn)); err == nil {
			db.SetMaxOpenConns(4)
			a.db = db
		}
	}
	return a
}

func hashKey(k string) string {
	h := sha256.Sum256([]byte(k))
	return hex.EncodeToString(h[:])
}

func apiKeyFromCtx(ctx context.Context) string {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return ""
	}
	if v := md.Get("x-api-key"); len(v) > 0 {
		return v[0]
	}
	return ""
}

// resolve looks up an active key (cache -> Postgres). found=false means no such active key.
func (a *Authenticator) resolve(ctx context.Context, key string) (*principal, bool, error) {
	h := hashKey(key)
	a.mu.RLock()
	if e, ok := a.cache[h]; ok && time.Now().Before(e.exp) {
		a.mu.RUnlock()
		return e.p, e.found, nil
	}
	a.mu.RUnlock()

	var pid, role string
	// effective role = the user's binding on this project (user-bound keys) else the key's own
	// role — the same COALESCE the Python resolver uses, so both planes agree.
	err := a.db.QueryRowContext(ctx,
		"SELECT k.project_id::text, COALESCE(rb.role, k.role)::text "+
			"FROM controlplane.api_keys k "+
			"LEFT JOIN controlplane.role_bindings rb "+
			"  ON rb.user_id = k.user_id AND rb.project_id = k.project_id "+
			"WHERE k.key_hash=$1 AND k.revoked_at IS NULL", h).Scan(&pid, &role)
	switch err {
	case nil:
		e := cacheEntry{&principal{pid, role}, true, time.Now().Add(a.ttl)}
		a.mu.Lock()
		a.cache[h] = e
		a.mu.Unlock()
		return e.p, true, nil
	case sql.ErrNoRows:
		e := cacheEntry{nil, false, time.Now().Add(a.ttl)}
		a.mu.Lock()
		a.cache[h] = e
		a.mu.Unlock()
		return nil, false, nil
	default:
		return nil, false, err // DB blip — caller fails open
	}
}

// decide is the pure authorization decision (unit-testable, no I/O).
func decide(keyPresent, dbEnabled, found bool, p *principal, cfg authConfig) error {
	if !keyPresent {
		if cfg.requireKey {
			return status.Error(codes.Unauthenticated, "API key required")
		}
		return nil
	}
	if !dbEnabled {
		return nil // presence-only (no DSN): a supplied key is accepted
	}
	if !found {
		return status.Error(codes.Unauthenticated, "invalid or revoked API key")
	}
	if cfg.projectID != "" && p.projectID != cfg.projectID {
		return status.Error(codes.PermissionDenied, "key not authorized for this project")
	}
	if roleRank[p.role] < roleRank[cfg.minRole] {
		return status.Error(codes.PermissionDenied, "insufficient role")
	}
	return nil
}

func (a *Authenticator) authorize(ctx context.Context) error {
	key := apiKeyFromCtx(ctx)
	if key == "" || a.db == nil {
		return decide(key != "", a.db != nil, false, nil, a.cfg)
	}
	p, found, err := a.resolve(ctx, key)
	if err != nil {
		return nil // fail open on a DB blip (availability over strictness)
	}
	return decide(true, true, found, p, a.cfg)
}

// StreamInterceptor guards streaming RPCs (Converse).
func (a *Authenticator) StreamInterceptor(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo,
	handler grpc.StreamHandler) error {
	if err := a.authorize(ss.Context()); err != nil {
		return err
	}
	return handler(srv, ss)
}

// UnaryInterceptor guards unary RPCs (Health).
func (a *Authenticator) UnaryInterceptor(ctx context.Context, req any, info *grpc.UnaryServerInfo,
	handler grpc.UnaryHandler) (any, error) {
	if err := a.authorize(ctx); err != nil {
		return nil, err
	}
	return handler(ctx, req)
}
