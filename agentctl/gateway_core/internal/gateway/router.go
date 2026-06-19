package gateway

import (
	"crypto/sha1"
	"encoding/binary"
	"sort"
)

// Backend mirrors agentctl/gateway/router.py:Backend. Plain struct - the routing decision
// is the load-bearing logic and must match the Python implementation byte-for-byte in behavior.
type Backend struct {
	ID         string
	Endpoint   string
	VersionTag string
	Weight     uint32
	IsCanary   bool
}

type RouteTable struct {
	Version uint64
	Primary []Backend
	Shadow  []Backend
}

// StableHash matches Python: first 8 bytes of sha1(session_id), big-endian.
func StableHash(s string) uint64 {
	h := sha1.Sum([]byte(s))
	return binary.BigEndian.Uint64(h[:8])
}

// WeightedPick: deterministic weighted choice over the cumulative ranges, backends sorted
// by ID so ranges are stable/reproducible (identical to weighted_pick in router.py).
func WeightedPick(backends []Backend, key uint64) Backend {
	eligible := make([]Backend, 0, len(backends))
	var total uint64
	for _, b := range backends {
		if b.Weight > 0 {
			eligible = append(eligible, b)
			total += uint64(b.Weight)
		}
	}
	if len(eligible) == 0 {
		eligible = backends
	}
	if total == 0 {
		return eligible[0]
	}
	sort.Slice(eligible, func(i, j int) bool { return eligible[i].ID < eligible[j].ID })
	point := key % total
	var cum uint64
	for _, b := range eligible {
		cum += uint64(b.Weight)
		if point < cum {
			return b
		}
	}
	return eligible[len(eligible)-1]
}

// Resolve pins a sticky canary arm per session (mix session with table version so a routing
// change re-rolls only NEW sessions). Returns the primary arm + the shadow set.
func (rt *RouteTable) Resolve(sessionID string) (Backend, []Backend) {
	key := StableHash(sessionID) ^ rt.Version
	return WeightedPick(rt.Primary, key), rt.Shadow
}

// DefaultRouteTable is the static bootstrap table (replace with a Postgres LISTEN/NOTIFY
// cache mirroring agentctl/gateway/pg_route_cache.py).
func DefaultRouteTable() *RouteTable {
	return &RouteTable{
		Version: 1,
		Primary: []Backend{
			{ID: "vA", Endpoint: "localhost:50051", VersionTag: "vA", Weight: 90},
			{ID: "vB", Endpoint: "localhost:50052", VersionTag: "vB", Weight: 10, IsCanary: true},
		},
		Shadow: []Backend{{ID: "shadow", Endpoint: "localhost:50053", VersionTag: "shadow"}},
	}
}
