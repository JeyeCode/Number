"""محدودکننده نرخ درخواست به‌ازای هر دامنه (Token Bucket)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    rate: float  # درخواست بر ثانیه
    capacity: float = 1.0
    tokens: float = 1.0
    updated: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def take(self, cost: float = 1.0) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.updated
                self.updated = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                wait = (cost - self.tokens) / max(self.rate, 0.01)
                await asyncio.sleep(min(wait, 5.0))


class RateLimiter:
    """نرخ‌سنج سراسری: هر دامنه سهم مستقل خود را دارد."""

    def __init__(self, default_rps: float = 1.0) -> None:
        self.default_rps = default_rps
        self._buckets: dict[str, _Bucket] = {}
        self._overrides: dict[str, float] = {}
        self._guard = asyncio.Lock()

    def set_rate(self, domain: str, rps: float) -> None:
        self._overrides[domain] = max(0.05, rps)

    def rate_for(self, domain: str) -> float:
        return self._overrides.get(domain, self.default_rps)

    async def acquire(self, domain: str, cost: float = 1.0) -> None:
        if not domain:
            return
        bucket = self._buckets.get(domain)
        if bucket is None:
            async with self._guard:
                bucket = self._buckets.get(domain)
                if bucket is None:
                    bucket = _Bucket(rate=self.rate_for(domain))
                    self._buckets[domain] = bucket
        await bucket.take(cost)
