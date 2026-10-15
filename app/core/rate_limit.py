"""Small in-memory sliding-window rate limiter.

Per-process only: fine for one worker / development. With several workers or
servers, swap the storage for Redis (same check() signature) or use a gateway rule.
Behind a reverse proxy, run uvicorn with --proxy-headers so request.client.host
is the real client IP, not the proxy's.
"""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from .errors import api_error


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                raise api_error(429, "RATE_LIMITED", "Too many attempts. Please try again later.")
            q.append(now)
            if len(self._hits) > 20000:          # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]


limiter = RateLimiter()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"