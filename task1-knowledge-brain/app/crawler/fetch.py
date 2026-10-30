"""Fetch one page politely: robots.txt, the rate limit, retries, and cheap re-checks.

- Retries 429, 500, 502, 503, 504 and network errors, up to MAX_ATTEMPTS tries in all. It waits
  as long as the server's Retry-After header asks (capped), or else backs off exponentially
  with a little random jitter, so two workers don't retry in lockstep.
- Sends the page's last ETag and Last-Modified back, so an unchanged page answers
  304 Not Modified with no body.
- Doesn't follow permanent redirects (301, 308): the worker records the move and queues the
  new URL. Temporary redirects (302, 303, 307) are followed, because the page keeps its address.
- Gives up on a body bigger than MAX_PAGE_BYTES.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import httpx2

from app.crawler.http import BodyTooLarge, read_limited
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules

MAX_ATTEMPTS = 5
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# A longer Retry-After is capped: the page goes back in the queue instead of blocking a worker
MAX_WAIT_SECONDS = 120.0
MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 5 * 1024 * 1024
TEMPORARY_REDIRECTS = frozenset({302, 303, 307})


class FetchError(Exception):
    """The page couldn't be fetched, even after retries. The worker tries again later."""


class FetchBlocked(FetchError):
    """robots.txt doesn't allow this URL, so it's never retried."""


@dataclass
class Fetched:
    url: str  # the URL that answered, after any temporary redirects
    status: int  # 200, 304, 301, 308, 404, 410, ...
    body: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    location: str | None = None  # where a redirect points, as a full URL
    retry_after: float | None = None  # seconds the server asked us to wait (429, 503)


class Fetcher:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        limiter: HostRateLimiter,
        robots: RobotsRules,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._robots = robots
        self._sleep = sleep

    async def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Fetched:
        """Fetch a page. Raises FetchBlocked or FetchError when there's nothing to keep."""
        for _ in range(MAX_REDIRECTS + 1):
            fetched = await self._get_with_retries(url, etag, last_modified)
            if fetched.status in TEMPORARY_REDIRECTS and fetched.location:
                # The validators belong to the old address, so the new one is fetched in full
                url, etag, last_modified = fetched.location, None, None
                continue
            return fetched
        raise FetchError(f"More than {MAX_REDIRECTS} redirects in a row")

    async def _get_with_retries(
        self, url: str, etag: str | None, last_modified: str | None
    ) -> Fetched:
        if not await self._robots.allowed(url):
            raise FetchBlocked("robots.txt doesn't allow this URL")
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.wait(url)
            try:
                fetched = await self._request(url, headers)
            except httpx2.TransportError as error:  # refused, reset, timed out
                if attempt == MAX_ATTEMPTS:
                    raise FetchError(f"Network error after {attempt} tries: {error}") from error
                await self._sleep(backoff(attempt))
                continue
            if fetched.status in RETRY_STATUSES:
                if attempt == MAX_ATTEMPTS:
                    raise FetchError(f"HTTP {fetched.status} after {attempt} tries")
                await self._sleep(max(backoff(attempt), fetched.retry_after or 0.0))
                continue
            return fetched
        raise AssertionError("unreachable: the last attempt returns or raises")

    async def _request(self, url: str, headers: dict[str, str]) -> Fetched:
        async with self._client.stream(
            "GET", url, headers=headers, follow_redirects=False
        ) as response:
            location = response.headers.get("location")
            fetched = Fetched(
                url=url,
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                location=urljoin(url, location) if location else None,
                retry_after=parse_retry_after(response.headers.get("retry-after")),
            )
            if response.status_code == 200:
                try:
                    fetched.body = await read_limited(response, MAX_PAGE_BYTES)
                except BodyTooLarge as error:
                    raise FetchError(str(error)) from error
            return fetched


def backoff(attempt: int) -> float:
    """1, 2, 4, 8... seconds (at most 60), plus up to a second of jitter."""
    return min(2 ** (attempt - 1), 60) + random.uniform(0, 1)


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After holds either seconds ("120") or an HTTP date. Capped at MAX_WAIT_SECONDS."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            return None
    return min(max(seconds, 0.0), MAX_WAIT_SECONDS)
