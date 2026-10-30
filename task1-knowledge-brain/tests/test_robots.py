"""robots.txt rules, including the RFC 9309 cases for a missing or failing file."""

from collections.abc import Callable

import httpx2
import pytest

from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules

SITE = "https://help.example"
# The same shape as the real help center's robots.txt
ROBOTS_TXT = b"""User-agent: *
Disallow: /support/search
Disallow: /helpdesk/
Allow: /helpdesk/attachments
Disallow: /*/hit$
"""


def make_rules(
    handler: Callable[[httpx2.Request], httpx2.Response], clock: Callable[[], float] | None = None
) -> RobotsRules:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    limiter = HostRateLimiter(1000)
    if clock is None:
        return RobotsRules(client, "TestBot/1.0", limiter)
    return RobotsRules(client, "TestBot/1.0", limiter, clock=clock)


async def test_rules_and_wildcards_are_followed() -> None:
    rules = make_rules(lambda request: httpx2.Response(200, content=ROBOTS_TXT))

    assert await rules.allowed(f"{SITE}/support/solutions/articles/5000558373-email-forwarders")
    assert not await rules.allowed(f"{SITE}/support/search?term=email")
    # The view-counter image inside every article: `Disallow: /*/hit$`
    assert not await rules.allowed(f"{SITE}/support/solutions/articles/5000558373-x/hit")
    # The longer, more specific Allow wins over Disallow: /helpdesk/
    assert await rules.allowed(f"{SITE}/helpdesk/attachments/12132171349")
    assert not await rules.allowed(f"{SITE}/helpdesk/tickets/1")


async def test_missing_robots_txt_allows_everything() -> None:
    rules = make_rules(lambda request: httpx2.Response(404))

    assert await rules.allowed(f"{SITE}/anything")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_failing_robots_txt_disallows_everything(status: int) -> None:
    rules = make_rules(lambda request: httpx2.Response(status))

    assert not await rules.allowed(f"{SITE}/support/solutions/articles/1-x")


async def test_unreachable_site_disallows_everything() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Connection refused", request=request)

    assert not await make_rules(refuse).allowed(f"{SITE}/page")


async def test_file_is_fetched_once_a_day_per_site() -> None:
    fetches: list[str] = []
    now = [0.0]

    def handler(request: httpx2.Request) -> httpx2.Response:
        fetches.append(str(request.url))
        return httpx2.Response(200, content=ROBOTS_TXT)

    rules = make_rules(handler, clock=lambda: now[0])

    await rules.allowed(f"{SITE}/a")
    await rules.allowed(f"{SITE}/b")
    assert fetches == [f"{SITE}/robots.txt"]

    now[0] += 24 * 60 * 60 + 1  # a day later the file is fetched again
    await rules.allowed(f"{SITE}/c")
    assert len(fetches) == 2
