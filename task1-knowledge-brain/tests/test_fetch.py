"""Fetching a page: retries, Retry-After, 304, redirects, robots.txt and the size cap."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx2
import pytest

from app.crawler import fetch
from app.crawler.fetch import FetchBlocked, Fetcher, FetchError, parse_retry_after
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules

SITE = "https://help.example"
PAGE = f"{SITE}/support/solutions/articles/1-labels"
NETWORK_ERROR = "network error"  # a reply that makes the fake site drop the connection


class FakeSite:
    """Answers each URL from its own list of replies, in order. Anything else is a 404,
    including robots.txt unless given, which means "no rules"."""

    def __init__(self, replies: dict[str, list[httpx2.Response | str]]) -> None:
        self.replies = replies
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        queue = self.replies.get(str(request.url))
        if not queue:
            return httpx2.Response(404)
        reply = queue.pop(0)
        if reply == NETWORK_ERROR:
            raise httpx2.ConnectError("Connection refused", request=request)
        assert isinstance(reply, httpx2.Response)
        return reply

    def page_requests(self) -> list[httpx2.Request]:
        return [request for request in self.requests if request.url.path != "/robots.txt"]


def make_fetcher(site: FakeSite, sleeps: list[float] | None = None) -> Fetcher:
    """A Fetcher on the fake site whose sleeps are recorded instead of waited."""
    waited = sleeps if sleeps is not None else []

    async def record_sleep(seconds: float) -> None:
        waited.append(seconds)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(site))
    limiter = HostRateLimiter(1000)
    robots = RobotsRules(client, "TestBot/1.0", limiter)
    return Fetcher(client, limiter, robots, sleep=record_sleep)


def html_page(headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8", **(headers or {})},
        content=b"<html><body>Labels</body></html>",
    )


async def test_page_comes_back_with_its_validators() -> None:
    modified = "Tue, 03 Oct 2023 01:12:05 GMT"
    site = FakeSite({PAGE: [html_page({"etag": '"v1"', "last-modified": modified})]})

    fetched = await make_fetcher(site).get(PAGE)

    assert (fetched.status, fetched.etag, fetched.last_modified) == (200, '"v1"', modified)
    assert fetched.body == b"<html><body>Labels</body></html>"
    assert fetched.content_type.startswith("text/html")


async def test_validators_are_sent_back_and_304_means_unchanged() -> None:
    modified = "Tue, 03 Oct 2023 01:12:05 GMT"
    site = FakeSite({PAGE: [httpx2.Response(304)]})

    fetched = await make_fetcher(site).get(PAGE, etag='"v1"', last_modified=modified)

    assert (fetched.status, fetched.body) == (304, b"")
    [request] = site.page_requests()
    assert request.headers["if-none-match"] == '"v1"'
    assert request.headers["if-modified-since"] == modified


async def test_429_waits_as_long_as_retry_after_asks() -> None:
    site = FakeSite({PAGE: [httpx2.Response(429, headers={"retry-after": "7"}), html_page()]})
    sleeps: list[float] = []

    fetched = await make_fetcher(site, sleeps).get(PAGE)

    assert fetched.status == 200
    assert sleeps == [7.0]  # Retry-After beats the 1 to 2 second backoff


async def test_server_errors_are_retried_with_growing_waits_then_given_up() -> None:
    site = FakeSite({PAGE: [httpx2.Response(503) for _ in range(5)]})
    sleeps: list[float] = []

    with pytest.raises(FetchError, match="HTTP 503 after 5 tries"):
        await make_fetcher(site, sleeps).get(PAGE)

    assert len(site.page_requests()) == 5
    assert len(sleeps) == 4
    assert sleeps == sorted(sleeps)  # 1, 2, 4, 8 seconds, each plus under a second of jitter


async def test_network_errors_are_retried() -> None:
    site = FakeSite({PAGE: [NETWORK_ERROR, html_page()]})

    fetched = await make_fetcher(site).get(PAGE)

    assert fetched.status == 200


async def test_permanent_redirect_is_reported_not_followed() -> None:
    moved = httpx2.Response(301, headers={"location": "/support/solutions/articles/1-new"})
    site = FakeSite({PAGE: [moved]})

    fetched = await make_fetcher(site).get(PAGE)

    assert (fetched.status, fetched.location) == (301, f"{SITE}/support/solutions/articles/1-new")
    assert len(site.page_requests()) == 1


async def test_temporary_redirect_is_followed() -> None:
    elsewhere = f"{SITE}/temporary"
    site = FakeSite(
        {PAGE: [httpx2.Response(302, headers={"location": elsewhere})], elsewhere: [html_page()]}
    )

    fetched = await make_fetcher(site).get(PAGE)

    assert (fetched.status, fetched.url) == (200, elsewhere)


async def test_url_blocked_by_robots_txt_is_never_requested() -> None:
    robots = httpx2.Response(200, content=b"User-agent: *\nDisallow: /support/\n")
    site = FakeSite({f"{SITE}/robots.txt": [robots]})

    with pytest.raises(FetchBlocked):
        await make_fetcher(site).get(PAGE)

    assert site.page_requests() == []


async def test_huge_page_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "MAX_PAGE_BYTES", 10)
    site = FakeSite({PAGE: [html_page()]})

    with pytest.raises(FetchError, match="larger than"):
        await make_fetcher(site).get(PAGE)


@pytest.mark.parametrize(
    ("value", "seconds"), [("7", 7.0), ("100000", 120.0), ("soon", None), (None, None)]
)
def test_retry_after_in_seconds(value: str | None, seconds: float | None) -> None:
    assert parse_retry_after(value) == seconds


def test_retry_after_as_a_date() -> None:
    in_a_minute = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)

    seconds = parse_retry_after(in_a_minute)

    assert seconds is not None and 55 <= seconds <= 60
