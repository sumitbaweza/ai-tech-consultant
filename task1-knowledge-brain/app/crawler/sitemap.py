"""Find every page URL in one or more sitemaps.

A sitemap is either a <urlset> that lists pages or a <sitemapindex> that lists more sitemaps.
discover() follows indexes down to MAX_DEPTH levels, never reads a sitemap twice (so an index
that lists itself can't loop), and keeps each sitemap's URLs in file order, so the preview can
show its first and last URL. A sitemap that can't be fetched or parsed comes back with its
error, and the others carry on.
"""

import logging
import re
import zlib
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx2

from app.crawler.http import MAX_BODY_BYTES, BodyTooLarge, read_limited
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.models import PageKind

logger = logging.getLogger(__name__)

MAX_DEPTH = 3  # the sitemap the user added, its child sitemaps, and their children
MAX_SITEMAPS = 500  # per discovery, so a runaway index can't keep the crawler busy for hours

# Freshdesk help centers keep articles under /solutions/articles/. Category, folder and home
# pages only list links, so they're tracked but not embedded. Other sites' pages count as
# articles.
LISTING_PATH = re.compile(r"/solutions(/folders/\d+|/\d+)?/?$|/support/home/?$")


class SitemapError(Exception):
    """This sitemap couldn't be read. The message says why, for the preview."""


@dataclass
class PageEntry:
    url: str
    lastmod: datetime | None


class ParsedSitemap(NamedTuple):
    is_index: bool
    pages: list[PageEntry]  # a <urlset>'s entries, in file order, duplicates kept
    children: list[str]  # a <sitemapindex>'s sitemap URLs


@dataclass
class FoundSitemap:
    url: str
    parent_url: str | None
    is_index: bool = False
    pages: list[PageEntry] = field(default_factory=list[PageEntry])
    children: list[str] = field(default_factory=list[str])
    error: str | None = None


def guess_kind(url: str) -> PageKind:
    return PageKind.LISTING if LISTING_PATH.search(urlsplit(url).path) else PageKind.ARTICLE


def parse_sitemap(data: bytes) -> ParsedSitemap:
    """Parse sitemap XML, gzipped or not. Files with or without the sitemap namespace work.

    Python's XML parser refuses entity-expansion bombs and never loads external entities, so
    parsing a stranger's file is safe.
    """
    if data[:2] == b"\x1f\x8b":  # the gzip signature, as in sitemap.xml.gz
        data = gunzip_limited(data)
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise SitemapError(f"Not valid XML: {error}") from error

    kind = _local_name(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"Not a sitemap: the top element is <{kind}>")

    pages: list[PageEntry] = []
    children: list[str] = []
    for entry in root:
        fields = {_local_name(child.tag): (child.text or "").strip() for child in entry}
        loc = fields.get("loc", "")
        if not loc.startswith(("http://", "https://")):
            continue  # an entry without a usable address
        if kind == "urlset":
            pages.append(PageEntry(loc, _parse_lastmod(fields.get("lastmod"))))
        else:
            children.append(loc)
    return ParsedSitemap(kind == "sitemapindex", pages, children)


def gunzip_limited(data: bytes, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Unzip, refusing to expand past max_bytes: a tiny .gz file can unzip to gigabytes."""
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)  # | 16: expect a gzip header
    try:
        unzipped = decompressor.decompress(data, max_bytes + 1)
    except zlib.error as error:
        raise SitemapError(f"Broken gzip data: {error}") from error
    if len(unzipped) > max_bytes:
        raise SitemapError(f"It unzips to more than {max_bytes:,} bytes")
    return unzipped


async def discover(
    urls: list[str],
    client: httpx2.AsyncClient,
    robots: RobotsRules,
    limiter: HostRateLimiter,
) -> list[FoundSitemap]:
    """Read the given sitemaps and every sitemap they list, breadth first, one at a time."""
    found: list[FoundSitemap] = []
    seen: set[str] = set()
    queue: deque[tuple[str, str | None, int]] = deque((url, None, 1) for url in urls)
    while queue:
        if len(found) >= MAX_SITEMAPS:
            logger.warning("Stopped after %d sitemaps; %d not read", MAX_SITEMAPS, len(queue))
            break
        url, parent_url, depth = queue.popleft()
        if url in seen:
            continue  # listed twice, or an index that points back at itself
        seen.add(url)

        sitemap = FoundSitemap(url, parent_url)
        found.append(sitemap)
        try:
            parsed = await _read(url, client, robots, limiter)
        except SitemapError as error:
            sitemap.error = str(error)
            continue
        sitemap.is_index, sitemap.pages, sitemap.children = parsed
        if sitemap.is_index and depth >= MAX_DEPTH:
            sitemap.error = f"Nested more than {MAX_DEPTH} levels deep; its sitemaps weren't read"
        elif sitemap.is_index:
            queue.extend((child, url, depth + 1) for child in parsed.children)
    return found


async def _read(
    url: str, client: httpx2.AsyncClient, robots: RobotsRules, limiter: HostRateLimiter
) -> ParsedSitemap:
    if not await robots.allowed(url):
        raise SitemapError("robots.txt doesn't allow reading this sitemap")
    await limiter.wait(url)
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise SitemapError(f"The server answered HTTP {response.status_code}")
            data = await read_limited(response)
    except httpx2.HTTPError as error:
        raise SitemapError(f"Couldn't fetch it: {error}") from error
    except BodyTooLarge as error:
        raise SitemapError(str(error)) from error
    return parse_sitemap(data)


def _local_name(tag: str) -> str:
    """'{http://www.sitemaps.org/schemas/sitemap/0.9}loc' -> 'loc'."""
    return tag.rsplit("}", 1)[-1]


def _parse_lastmod(value: str | None) -> datetime | None:
    """W3C dates such as 2026-10-25 or 2026-10-25T08:01:03+05:30. Anything else gives None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
