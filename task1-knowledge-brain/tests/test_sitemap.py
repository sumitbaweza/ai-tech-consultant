"""Reading sitemaps: parsing, nested indexes, and what happens when one is broken."""

import gzip
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest

from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import (
    FoundSitemap,
    SitemapError,
    discover,
    guess_kind,
    gunzip_limited,
    parse_sitemap,
)
from app.models import PageKind

SITE = "https://help.example"


def urlset(*urls: str) -> bytes:
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return (
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    ).encode()


def index(*urls: str) -> bytes:
    entries = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in urls)
    return (
        f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</sitemapindex>'
    ).encode()


# --- Parsing ------------------------------------------------------------------------------


def test_urlset_keeps_file_order_and_reads_lastmod() -> None:
    parsed = parse_sitemap(
        b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://help.example/b</loc><lastmod>2026-10-25T08:01:03+00:00</lastmod></url>
          <url><loc>https://help.example/a</loc><lastmod>2026-07-15</lastmod></url>
          <url><loc>  https://help.example/c  </loc></url>
          <url><loc>mailto:someone@example.com</loc></url>
        </urlset>"""
    )

    assert not parsed.is_index
    assert [page.url for page in parsed.pages] == [f"{SITE}/b", f"{SITE}/a", f"{SITE}/c"]
    assert parsed.pages[0].lastmod == datetime(2026, 10, 25, 8, 1, 3, tzinfo=UTC)
    assert parsed.pages[1].lastmod == datetime(2026, 7, 15, tzinfo=UTC)
    assert parsed.pages[2].lastmod is None


def test_sitemap_without_the_namespace_still_parses() -> None:
    parsed = parse_sitemap(b"<urlset><url><loc>https://help.example/x</loc></url></urlset>")

    assert [page.url for page in parsed.pages] == [f"{SITE}/x"]


def test_index_lists_child_sitemaps() -> None:
    parsed = parse_sitemap(index(f"{SITE}/a.xml", f"{SITE}/b.xml"))

    assert parsed.is_index
    assert parsed.children == [f"{SITE}/a.xml", f"{SITE}/b.xml"]


def test_broken_xml_is_a_clear_error() -> None:
    with pytest.raises(SitemapError, match="Not valid XML"):
        parse_sitemap(b"<urlset><url><loc>https://help.example/x</loc></url>")


def test_an_html_page_is_not_a_sitemap() -> None:
    with pytest.raises(SitemapError, match="Not a sitemap"):
        parse_sitemap(b"<html><body>Page not found</body></html>")


def test_gzipped_sitemap_is_unzipped() -> None:
    parsed = parse_sitemap(gzip.compress(urlset(f"{SITE}/x")))

    assert [page.url for page in parsed.pages] == [f"{SITE}/x"]


def test_gzip_bomb_is_refused() -> None:
    with pytest.raises(SitemapError, match="unzips to more than"):
        gunzip_limited(gzip.compress(b"0" * 10_000), max_bytes=1_000)


def test_entity_expansion_bomb_is_refused() -> None:
    # "Billion laughs": 9 levels of entities that would expand to gigabytes
    levels = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{n} "{f"&lol{n - 1};" * 10}">' for n in range(1, 10)
    ]
    bomb = f'<?xml version="1.0"?><!DOCTYPE urlset [{"".join(levels)}]><urlset>&lol9;</urlset>'

    with pytest.raises(SitemapError):
        parse_sitemap(bomb.encode())


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (f"{SITE}/support/solutions/articles/5000558373-email-forwarders", PageKind.ARTICLE),
        (f"{SITE}/support/solutions/folders/12000005694", PageKind.LISTING),
        (f"{SITE}/support/solutions/12000001517", PageKind.LISTING),
        (f"{SITE}/support/solutions", PageKind.LISTING),
        (f"{SITE}/support/home", PageKind.LISTING),
        ("https://docs.other.example/getting-started", PageKind.ARTICLE),
    ],
)
def test_guess_kind_from_the_url(url: str, kind: PageKind) -> None:
    assert guess_kind(url) == kind


# --- Discovery against a fake website -------------------------------------------------------


def fake_site(files: dict[str, bytes | int]) -> Callable[[httpx2.Request], httpx2.Response]:
    """A fake web server: URL -> body, or URL -> HTTP status. Anything else answers 404,
    including robots.txt unless given, which means "no rules"."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        found = files.get(str(request.url), 404)
        if isinstance(found, int):
            return httpx2.Response(found)
        return httpx2.Response(200, content=found)

    return handle


async def run_discover(files: dict[str, bytes | int], urls: list[str]) -> list[FoundSitemap]:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake_site(files))) as client:
        limiter = HostRateLimiter(1000)
        robots = RobotsRules(client, "TestBot/1.0", limiter)
        return await discover(urls, client, robots, limiter)


async def test_nested_index_is_followed_and_duplicates_are_kept() -> None:
    root, a, b = f"{SITE}/sitemap.xml", f"{SITE}/a.xml", f"{SITE}/b.xml"
    files: dict[str, bytes | int] = {
        root: index(a, b),
        a: urlset(f"{SITE}/p1", f"{SITE}/p2"),
        b: urlset(f"{SITE}/p2", f"{SITE}/p3"),
    }

    found = await run_discover(files, [root])

    assert [(s.url, s.parent_url, s.is_index) for s in found] == [
        (root, None, True),
        (a, root, False),
        (b, root, False),
    ]
    assert [page.url for page in found[2].pages] == [f"{SITE}/p2", f"{SITE}/p3"]


async def test_an_index_that_lists_itself_is_read_once() -> None:
    root, a = f"{SITE}/sitemap.xml", f"{SITE}/a.xml"
    found = await run_discover({root: index(root, a), a: urlset(f"{SITE}/p1")}, [root])

    assert [s.url for s in found] == [root, a]


async def test_indexes_nested_too_deep_stop_with_an_error() -> None:
    files: dict[str, bytes | int] = {
        f"{SITE}/1.xml": index(f"{SITE}/2.xml"),
        f"{SITE}/2.xml": index(f"{SITE}/3.xml"),
        f"{SITE}/3.xml": index(f"{SITE}/4.xml"),
        f"{SITE}/4.xml": urlset(f"{SITE}/p1"),
    }

    found = await run_discover(files, [f"{SITE}/1.xml"])

    assert [s.url for s in found] == [f"{SITE}/1.xml", f"{SITE}/2.xml", f"{SITE}/3.xml"]
    assert "Nested more than 3 levels" in (found[2].error or "")


async def test_one_broken_sitemap_does_not_stop_the_others() -> None:
    root = f"{SITE}/sitemap.xml"
    down, broken, good = f"{SITE}/down.xml", f"{SITE}/broken.xml", f"{SITE}/good.xml"
    files: dict[str, bytes | int] = {
        root: index(down, broken, good),
        down: 500,
        broken: b"<urlset><url>",
        good: urlset(f"{SITE}/p1"),
    }

    found = await run_discover(files, [root])

    errors = {s.url: s.error for s in found}
    assert "HTTP 500" in (errors[down] or "")
    assert "Not valid XML" in (errors[broken] or "")
    assert errors[good] is None
    assert [page.url for page in found[3].pages] == [f"{SITE}/p1"]


async def test_a_sitemap_blocked_by_robots_txt_is_not_fetched() -> None:
    blocked = f"{SITE}/private/sitemap.xml"
    files: dict[str, bytes | int] = {
        f"{SITE}/robots.txt": b"User-agent: *\nDisallow: /private/\n",
        blocked: urlset(f"{SITE}/p1"),
    }

    [sitemap] = await run_discover(files, [blocked])

    assert "robots.txt" in (sitemap.error or "")
    assert sitemap.pages == []
