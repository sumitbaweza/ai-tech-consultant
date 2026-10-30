"""robots.txt rules, fetched once per site and cached.

RFC 9309: a robots.txt that answers 4xx means "no rules", so everything is allowed. A 5xx, a
429 or no answer at all means the crawler must assume everything is disallowed and try again
later. protego parses the file, including the * and $ wildcards that Python's built-in
robotparser ignores; this help center uses `Disallow: /*/hit$`.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2
from protego import Protego

from app.crawler.ratelimit import HostRateLimiter

ALLOW_ALL = Protego.parse("")
DISALLOW_ALL = Protego.parse("User-agent: *\nDisallow: /")

# A good file is reused for a day (RFC 9309's limit); after a failed fetch, try again sooner
KEEP_SECONDS = 24 * 60 * 60
RETRY_AFTER_FAILURE_SECONDS = 10 * 60


@dataclass
class _Cached:
    rules: Protego
    expires_at: float


class RobotsRules:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        user_agent: str,
        limiter: HostRateLimiter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        # robots.txt groups name the product token, the part of the User-Agent before "/"
        self._agent = user_agent.split("/")[0]
        self._limiter = limiter
        self._clock = clock
        self._cache: dict[str, _Cached] = {}
        # One lock for all sites: simple, and the crawler only visits a couple of sites
        self._lock = asyncio.Lock()

    async def allowed(self, url: str) -> bool:
        rules = await self._rules_for(url)
        return rules.can_fetch(url, self._agent)

    async def _rules_for(self, url: str) -> Protego:
        parts = urlsplit(url)
        site = f"{parts.scheme}://{parts.netloc}"
        async with self._lock:  # two workers asking at once still fetch the file only once
            cached = self._cache.get(site)
            if cached is None or cached.expires_at <= self._clock():
                rules, keep_seconds = await self._fetch(site)
                cached = _Cached(rules, self._clock() + keep_seconds)
                self._cache[site] = cached
            return cached.rules

    async def _fetch(self, site: str) -> tuple[Protego, float]:
        robots_url = f"{site}/robots.txt"
        await self._limiter.wait(robots_url)
        try:
            response = await self._client.get(robots_url)
        except httpx2.HTTPError:
            return DISALLOW_ALL, RETRY_AFTER_FAILURE_SECONDS
        if response.status_code == 429 or response.status_code >= 500:
            return DISALLOW_ALL, RETRY_AFTER_FAILURE_SECONDS
        if response.status_code >= 400:
            return ALLOW_ALL, KEEP_SECONDS
        return Protego.parse(response.text), KEEP_SECONDS
