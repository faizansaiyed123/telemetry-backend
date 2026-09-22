"""Small process-local sliding-window rate limiter.

This is deliberately dependency-free. It provides abuse protection for a
single-process deployment while keeping the limiter replaceable by a shared
store later if the service is horizontally scaled.
"""

from __future__ import annotations

from collections import defaultdict, deque
from math import ceil
from threading import Lock
from time import monotonic


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds a configured request window."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__("Too many requests")


class SlidingWindowRateLimiter:
    """Thread-safe fixed-window counters implemented with timestamp deques."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                retry_after = ceil(events[0] + window_seconds - now)
                raise RateLimitExceeded(retry_after)

            events.append(now)

    def clear(self, key: str) -> None:
        """Clear one caller window after a successful authentication."""
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowRateLimiter()
