"""
Simple caching layer to reduce load on GoldenGate REST API.

This is NOT for cost optimization (Azure Copilot Enterprise has different pricing).
This is to prevent hammering your GoldenGate servers with repeated identical queries.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class SimpleCache:
    """
    Simple in-memory cache with TTL.

    Use case: User asks "show me lag" 3 times in 2 minutes.
    Without cache: 3 API calls to GoldenGate
    With cache: 1 API call, 2 cache hits

    This protects your GoldenGate infrastructure, not your wallet.
    """

    def __init__(self, ttl_seconds: int = 30):
        """
        Initialize cache.

        Args:
            ttl_seconds: Time to live for cached data (default 30 seconds)
                        Short TTL ensures data is fresh for operations
        """
        self.cache: Dict[str, Tuple[Any, datetime]] = {}
        self.ttl = timedelta(seconds=ttl_seconds)
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key in self.cache:
            value, timestamp = self.cache[key]
            if datetime.utcnow() - timestamp < self.ttl:
                self.hits += 1
                logger.debug(f"Cache HIT: {key}")
                return value
            else:
                # Expired, remove it
                del self.cache[key]

        self.misses += 1
        logger.debug(f"Cache MISS: {key}")
        return None

    def set(self, key: str, value: Any):
        """Set cached value with current timestamp."""
        self.cache[key] = (value, datetime.utcnow())
        logger.debug(f"Cache SET: {key}")

    def invalidate(self, key: str):
        """Invalidate specific cache entry."""
        if key in self.cache:
            del self.cache[key]
            logger.debug(f"Cache INVALIDATE: {key}")

    def clear(self):
        """Clear all cache."""
        self.cache.clear()
        logger.info("Cache cleared")

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0

        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(hit_rate, 1),
            "cached_items": len(self.cache)
        }


class RateLimiter:
    """
    Rate limiter to prevent overwhelming GoldenGate REST API.

    Use case: Batch operation on 50 processes shouldn't DoS your GoldenGate server.

    This is infrastructure protection, not cost optimization.
    """

    def __init__(self, max_concurrent: int = 10, requests_per_second: int = 20):
        """
        Initialize rate limiter.

        Args:
            max_concurrent: Maximum concurrent requests (default 10)
            requests_per_second: Maximum requests per second (default 20)
        """
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.rps = requests_per_second
        self.last_request_time = datetime.utcnow()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Acquire rate limit token."""
        # Limit concurrent requests
        await self.semaphore.acquire()

        # Limit requests per second
        async with self.lock:
            now = datetime.utcnow()
            time_since_last = (now - self.last_request_time).total_seconds()
            min_interval = 1.0 / self.rps

            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                await asyncio.sleep(sleep_time)

            self.last_request_time = datetime.utcnow()

    def release(self):
        """Release rate limit token."""
        self.semaphore.release()


def cached(ttl_seconds: int = 30):
    """
    Decorator to cache async function results.

    Args:
        ttl_seconds: Cache TTL in seconds

    Example:
        @cached(ttl_seconds=30)
        async def get_deployment_health(deployment: str):
            return await expensive_api_call(deployment)
    """
    cache = SimpleCache(ttl_seconds=ttl_seconds)

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{args}:{kwargs}"

            # Check cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Cache miss - call function
            result = await func(*args, **kwargs)

            # Store in cache
            cache.set(cache_key, result)

            return result

        # Expose cache for manual invalidation
        wrapper.cache = cache
        return wrapper

    return decorator
