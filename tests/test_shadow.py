"""Pure-asyncio unit tests for ShadowChannel (no gRPC). Covers the lossy-offer invariant, the
graceful close() drain, and the abort-path cancel() that tears down a STUCK shadow without hanging -
the leak the proxy's _teardown relies on when a client disconnects mid-stream."""
from __future__ import annotations

import asyncio

import pytest

from agentctl.gateway.shadow import ShadowChannel


class RecordingCall:
    """A well-behaved shadow call: records writes, yields canned responses, then half-closes."""
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.written: list = []
        self.done = False
        self.cancelled = False

    async def write(self, b):
        self.written.append(b)

    async def done_writing(self):
        self.done = True

    async def __aiter__(self):
        for r in self.responses:
            yield r

    def cancel(self):
        self.cancelled = True


class BlockingCall:
    """A stuck shadow: write() and the response stream both block forever (backend not reading)."""
    def __init__(self):
        self.cancelled = False

    async def write(self, b):
        await asyncio.Event().wait()

    async def done_writing(self):
        pass

    async def __aiter__(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover - never reached

    def cancel(self):
        self.cancelled = True


def test_offer_drops_when_full_and_counts():
    async def go():
        # Offers happen synchronously before the writer task is ever scheduled, so the bounded queue
        # fills deterministically: maxsize accepted, the rest dropped (never blocking the caller).
        sc = ShadowChannel("s", BlockingCall(), maxsize=2)
        for i in range(5):
            sc.offer(i)
        assert sc.sent == 0 and sc.dropped == 3
        sc.cancel()
    asyncio.run(go())


def test_close_drains_writes_done_and_collects_received():
    async def go():
        call = RecordingCall(responses=[b"r1", b"r2"])
        sc = ShadowChannel("s", call, maxsize=64)
        sc.offer(b"a")
        sc.offer(b"b")
        await sc.close()
        assert call.written == [b"a", b"b"]   # buffered frames flushed in order
        assert call.done is True              # half-closed via done_writing
        assert sc.sent == 2
        assert sc.received == 2               # shadow responses counted (divergence signal)
    asyncio.run(go())


def test_cancel_tears_down_stuck_shadow_without_hanging():
    async def go():
        call = BlockingCall()
        sc = ShadowChannel("s", call, maxsize=64)
        sc.offer(b"a")
        await asyncio.sleep(0)        # let the writer pull a frame and block on write
        sc.cancel()                   # must return immediately (the test completing proves it)
        assert call.cancelled is True
        for t in (sc._writer, sc._drainer):
            with pytest.raises(asyncio.CancelledError):
                await t               # both background tasks are torn down, not leaked
    asyncio.run(go())


if __name__ == "__main__":
    test_offer_drops_when_full_and_counts()
    test_close_drains_writes_done_and_collects_received()
    test_cancel_tears_down_stuck_shadow_without_hanging()
    print("shadow tests passed")
