package gateway

import (
	"testing"
	"time"

	acpv1 "github.com/agentctl/gateway_core/gen/acpv1"
)

// A deliberately-stuck shadow backend must NOT block offer() (which the primary pump calls), and
// offers past the bounded buffer must drop-and-count rather than wait. This is the invariant the
// docs claim ("a slow shadow can never flow-control the primary") proven for the Go path.
func TestShadowPipeDropsWhenStalled(t *testing.T) {
	release := make(chan struct{})
	send := func(_ *acpv1.Frame) error { <-release; return nil } // simulate a stuck shadow

	p := newShadowPipe(send)

	const n = shadowBuffer + 500
	done := make(chan struct{})
	go func() {
		for i := 0; i < n; i++ {
			p.offer(&acpv1.Frame{})
		}
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("offer() blocked on a stalled shadow - a slow shadow can throttle the primary")
	}

	if p.Dropped() == 0 {
		t.Fatal("expected drop-on-full with a stalled drain, got dropped=0")
	}
	if got := p.Sent() + p.Dropped(); got > int64(n) {
		t.Fatalf("sent+dropped (%d) exceeds offered (%d)", got, n)
	}
	close(release) // unblock the drain so the goroutine can exit cleanly
}

// With a fast (non-blocking) shadow, every offered frame is delivered and nothing is dropped.
func TestShadowPipeDeliversWhenFast(t *testing.T) {
	got := make(chan struct{}, 1024)
	send := func(_ *acpv1.Frame) error { got <- struct{}{}; return nil }

	p := newShadowPipe(send)
	const n = 100
	for i := 0; i < n; i++ {
		p.offer(&acpv1.Frame{})
	}
	p.close()

	deadline := time.After(2 * time.Second)
	for i := 0; i < n; i++ {
		select {
		case <-got:
		case <-deadline:
			t.Fatalf("only %d/%d frames delivered through a fast shadow", i, n)
		}
	}
	if p.Dropped() != 0 {
		t.Fatalf("fast shadow should drop nothing, got dropped=%d", p.Dropped())
	}
}
