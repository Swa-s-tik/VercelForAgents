"""Shadow mirroring (Vertical B): lossy fan-out + discard-drain.

INVARIANT: the shadow path never throttles the primary. Frames are OFFERED to a bounded
queue; on overflow we DROP and count (lossy), so a slow/failing shadow can never apply
backpressure to the client. Shadow responses are read and discarded.
"""
from __future__ import annotations

import asyncio


class ShadowChannel:
    def __init__(self, name: str, call, maxsize: int = 64):
        self.name = name
        self.call = call
        self.q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.sent = 0
        self.dropped = 0
        self.received = 0
        self._writer = asyncio.create_task(self._write_loop())
        self._drainer = asyncio.create_task(self._drain_loop())

    def offer(self, frame) -> None:
        """Non-blocking. Drop-on-full so the primary is never slowed."""
        try:
            self.q.put_nowait(frame)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _write_loop(self) -> None:
        try:
            while True:
                frame = await self.q.get()
                if frame is None:
                    break
                await self.call.write(frame)
                self.sent += 1
            await self.call.done_writing()
        except Exception:
            pass  # shadow failures are isolated; never propagate to the primary

    async def _drain_loop(self) -> None:
        try:
            async for _ in self.call:   # responses discarded (comparison hook would go here)
                self.received += 1
        except Exception:
            pass

    async def close(self) -> None:
        try:
            self.q.put_nowait(None)
        except asyncio.QueueFull:
            self._writer.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(self._writer, self._drainer, return_exceptions=True), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._writer.cancel()
            self._drainer.cancel()

    def cancel(self) -> None:
        """Synchronous, non-blocking teardown for the ABORT path (client disconnected mid-stream).
        Stops the writer/drainer tasks and cancels the underlying shadow RPC so neither the tasks nor
        the gRPC call leak. Unlike close() this never awaits, so a stuck shadow can't delay client
        cleanup. Idempotent and safe to call after close() (cancelling a finished task is a no-op)."""
        self._writer.cancel()
        self._drainer.cancel()
        cancel = getattr(self.call, "cancel", None)
        if cancel is not None:
            try:
                cancel()
            except Exception:
                pass
