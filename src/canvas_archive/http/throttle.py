"""Adaptive rate-limit governor driven by Canvas's own headers.

Canvas runs a leaky bucket per API token and reports `x-rate-limit-remaining` and
`x-request-cost` on every response. Reading those beats guessing at sleep intervals.

The failure mode observed in practice is *not* a clean 429: a hammered connection is
accepted and then never answered. That is why there is an explicit read timeout in the
client and a circuit breaker here -- a stall must become a retryable event, never a
silent freeze.
"""

from __future__ import annotations

import asyncio
import random

SLOW_DOWN_BELOW = 200.0
CRITICAL_BELOW = 100.0
LEAK_RATE_PER_SEC = 10.0

# Consecutive failures before we stop hammering and wait for the server to recover.
BREAKER_THRESHOLD = 5
BREAKER_PAUSES = (30.0, 60.0, 120.0)


class Throttle:
    def __init__(self, concurrency: int = 6) -> None:
        self.max_concurrency = max(1, concurrency)
        self._sem = asyncio.Semaphore(self.max_concurrency)
        self._lock = asyncio.Lock()
        self.remaining: float | None = None
        self.throttled = False
        self.consecutive_failures = 0
        self._breaker_trips = 0
        # Set by the client so the UI can show *why* a run looks slow.
        self.on_wait = None

    def _notify(self, message: str, seconds: float) -> None:
        if self.on_wait:
            try:
                self.on_wait(message, seconds)
            except Exception:
                pass

    async def acquire(self) -> None:
        await self._sem.acquire()
        delay = 0.0
        async with self._lock:
            if self.remaining is not None and self.remaining < SLOW_DOWN_BELOW:
                deficit = SLOW_DOWN_BELOW - self.remaining
                delay = min(deficit / LEAK_RATE_PER_SEC, 30.0)
                self.throttled = True
        if delay:
            self._notify("Canvas is rate-limiting us", delay)
            await asyncio.sleep(delay)

    def release(self) -> None:
        self._sem.release()

    def observe(self, headers) -> None:
        raw = headers.get("x-rate-limit-remaining")
        if raw is None:
            return
        try:
            self.remaining = float(raw)
        except ValueError:
            return
        self.throttled = self.remaining < SLOW_DOWN_BELOW

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self._breaker_trips = 0

    async def record_failure(self) -> None:
        """Trip a circuit breaker rather than burning the queue against a sick server."""
        self.consecutive_failures += 1
        if self.consecutive_failures < BREAKER_THRESHOLD:
            return
        pause = BREAKER_PAUSES[min(self._breaker_trips, len(BREAKER_PAUSES) - 1)]
        self._breaker_trips += 1
        self.consecutive_failures = 0
        self._notify("Too many failures in a row, pausing to let Canvas recover", pause)
        await asyncio.sleep(pause)

    async def backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Full-jitter exponential backoff, honouring Retry-After when present."""
        if retry_after:
            try:
                seconds = min(float(retry_after), 120.0)
                self._notify("Canvas asked us to wait", seconds)
                await asyncio.sleep(seconds)
                return
            except ValueError:
                pass
        ceiling = min(2.0 * (2**attempt), 60.0)
        await asyncio.sleep(random.uniform(0.5, ceiling))
