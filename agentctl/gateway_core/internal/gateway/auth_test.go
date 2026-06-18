package gateway

import (
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func codeOf(err error) codes.Code {
	if err == nil {
		return codes.OK
	}
	return status.Code(err)
}

// hashKey must match Python's sha256 hexdigest so a key created by the control plane validates on
// the Go gateway. This is the seeded bootstrap key's hash (schema_postgres.sql).
func TestHashKeyMatchesPython(t *testing.T) {
	got := hashKey("actl_dev_bootstrap_0000000000000000")
	want := "4567f685fabf4123b67e3cda67d3e10b56fbb1dbd5e5df36fe6626429ca27b0a"
	if got != want {
		t.Fatalf("hashKey mismatch:\n got  %s\n want %s", got, want)
	}
}

func TestDecide(t *testing.T) {
	owner := &principal{projectID: "p1", role: "owner"}
	viewer := &principal{projectID: "p1", role: "viewer"}
	cases := []struct {
		name                       string
		keyPresent, dbEnabled, found bool
		p                          *principal
		cfg                        authConfig
		want                       codes.Code
	}{
		{"no key, not required", false, true, false, nil, authConfig{}, codes.OK},
		{"no key, required", false, true, false, nil, authConfig{requireKey: true}, codes.Unauthenticated},
		{"key, no db -> presence ok", true, false, false, nil, authConfig{}, codes.OK},
		{"key not found", true, true, false, nil, authConfig{minRole: "viewer"}, codes.Unauthenticated},
		{"valid owner, project ok", true, true, true, owner, authConfig{minRole: "viewer", projectID: "p1"}, codes.OK},
		{"valid viewer meets viewer", true, true, true, viewer, authConfig{minRole: "viewer", projectID: "p1"}, codes.OK},
		{"wrong project", true, true, true, owner, authConfig{minRole: "viewer", projectID: "p2"}, codes.PermissionDenied},
		{"insufficient role", true, true, true, viewer, authConfig{minRole: "admin", projectID: "p1"}, codes.PermissionDenied},
	}
	for _, c := range cases {
		if got := codeOf(decide(c.keyPresent, c.dbEnabled, c.found, c.p, c.cfg)); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}
