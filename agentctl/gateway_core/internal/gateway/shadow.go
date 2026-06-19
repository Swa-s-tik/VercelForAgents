package gateway

import "sync/atomic"

// shadowBuffer bounds each shadow lane. Large enough to absorb normal jitter, small enough that a
// truly stuck shadow sheds load instead of growing memory without bound.
const shadowBuffer = 256

// shadowPipe is a bounded, drop-on-full fan-out lane to a single shadow backend. It is generic over
// the frame representation so the same drop-on-full logic serves both the typed path (*acpv1.Frame)
// and the zero-copy path (*wire.RawFrame).
//
// This is the asymmetry the design depends on: the PRIMARY path is lossless (a slow client/backend
// propagates backpressure and slows only its own stream), while a SHADOW is LOSSY BY DESIGN so a
// slow or stuck shadow backend can never flow-control the primary. offer() never blocks — on a full
// buffer it drops and counts — and a single dedicated goroutine is the only place that may block on
// the (potentially slow) backend Send. This matches agentctl/gateway/shadow.py (ShadowChannel).
type shadowPipe[T any] struct {
	ch      chan T
	sent    int64
	dropped int64
}

// newShadowPipe starts the drain goroutine that forwards buffered frames to the shadow backend via
// send. A send error drops the frame (shadow responses are discarded anyway) but keeps draining, so
// offer() can never be blocked by a failing shadow. T is inferred from send's argument.
func newShadowPipe[T any](send func(T) error) *shadowPipe[T] {
	p := &shadowPipe[T]{ch: make(chan T, shadowBuffer)}
	go func() {
		for f := range p.ch {
			if err := send(f); err != nil {
				continue
			}
			atomic.AddInt64(&p.sent, 1)
		}
	}()
	return p
}

// offer enqueues a frame WITHOUT EVER BLOCKING the caller (the primary pump goroutine). When the
// buffer is full it drops the frame and increments the dropped counter — the whole point: a slow
// shadow sheds its own load and the primary stream is never throttled.
func (p *shadowPipe[T]) offer(f T) {
	select {
	case p.ch <- f:
	default:
		atomic.AddInt64(&p.dropped, 1)
	}
}

// close stops the drain goroutine once the already-buffered frames have flushed.
func (p *shadowPipe[T]) close() { close(p.ch) }

// Sent / Dropped expose the lane's counters (mirrors the Python proxy's shadow_sent/shadow_dropped).
func (p *shadowPipe[T]) Sent() int64    { return atomic.LoadInt64(&p.sent) }
func (p *shadowPipe[T]) Dropped() int64 { return atomic.LoadInt64(&p.dropped) }
