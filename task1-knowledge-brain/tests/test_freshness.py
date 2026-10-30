"""Scheduled sitemap checks, against the test database and a fake website."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import httpx2
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.main import app
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import discover
from app.db import get_session
from app.freshness import RETRY_AFTER, check_sitemap, claim_due_sitemap
from app.ingest import confirm_ingestion, save_discovery
from app.models import (
    IngestionRun,
    Page,
    PageStatus,
    Retention,
    RunKind,
    RunStatus,
    Schedule,
    Sitemap,
)
from tests.test_sitemap import fake_site, index, urlset

pytestmark = pytest.mark.db  # every test here needs Postgres

SITE = "https://help.example"
SITEMAP = f"{SITE}/sitemap.xml"
P1, P2, P3 = (f"{SITE}/support/solutions/articles/{n}" for n in (1, 2, 3))

type Files = dict[str, bytes | int]


@asynccontextmanager
async def fake_web(
    files: Files,
) -> AsyncGenerator[tuple[httpx2.AsyncClient, RobotsRules, HostRateLimiter], None]:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake_site(files))) as client:
        limiter = HostRateLimiter(1000)
        yield client, RobotsRules(client, "TestBot/1.0", limiter), limiter


async def crawled(session: AsyncSession, files: Files) -> Sitemap:
    """A sitemap whose first crawl has finished, every page active, and whose check is due."""
    async with fake_web(files) as (client, robots, limiter):
        found = await discover([SITEMAP], client, robots, limiter)
    [root_id] = await save_discovery(session, found, Schedule.DAILY, Retention.AFTER_2)
    await confirm_ingestion(session, [root_id])
    await session.execute(update(Page).values(status=PageStatus.ACTIVE, fetch_requested_at=None))
    sitemap = await session.get(Sitemap, root_id)
    assert sitemap is not None
    sitemap.next_check_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    return sitemap


async def claim_and_check(session: AsyncSession, files: Files) -> IngestionRun | None:
    """What the worker's check task does when a check is due, against the fake site."""
    sitemap = await claim_due_sitemap(session)
    assert sitemap is not None
    async with fake_web(files) as (client, robots, limiter):
        return await check_sitemap(session, sitemap, client, robots, limiter)


async def pages_by_url(session: AsyncSession) -> dict[str, Page]:
    pages = await session.scalars(select(Page).execution_options(populate_existing=True))
    return {page.url: page for page in pages}


def about(actual: datetime | None, expected: datetime) -> bool:
    return actual is not None and abs(actual - expected) < timedelta(seconds=30)


async def test_only_due_sitemaps_that_the_user_added_are_claimed(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    settings = {"schedule": Schedule.DAILY, "retention": Retention.AFTER_2}
    due = Sitemap(url=SITEMAP, next_check_at=now - timedelta(hours=2), **settings)
    db_session.add_all(
        [
            due,
            Sitemap(url=f"{SITE}/later.xml", next_check_at=now + timedelta(hours=1), **settings),
            Sitemap(url=f"{SITE}/unconfirmed.xml", **settings),  # no next_check_at
        ]
    )
    await db_session.flush()
    # A sitemap inside another is checked with it, however overdue it looks
    child = Sitemap(url=f"{SITE}/child.xml", parent_id=due.id, next_check_at=now, **settings)
    db_session.add(child)
    await db_session.commit()

    claimed = await claim_due_sitemap(db_session)
    claimed_again = await claim_due_sitemap(db_session)

    assert claimed is not None and claimed.url == SITEMAP
    assert about(claimed.next_check_at, now + RETRY_AFTER)  # the claim moved it an hour ahead
    assert claimed_again is None


async def test_a_check_adds_new_urls_and_fetches_known_pages_again(
    db_session: AsyncSession,
) -> None:
    sitemap = await crawled(db_session, {SITEMAP: urlset(P1, P2)})
    await db_session.execute(update(Page).where(Page.url == P2).values(status=PageStatus.ARCHIVED))
    await db_session.commit()
    started = datetime.now(UTC)

    # P2 is gone from the sitemap, and P3 is new
    run = await claim_and_check(db_session, {SITEMAP: urlset(P1, P3)})

    assert run is not None
    assert (run.kind, run.status, run.total_pages) == (RunKind.REFRESH, RunStatus.RUNNING, 2)
    pages = await pages_by_url(db_session)
    # A known page stays active, so searchable, while it waits to be fetched again
    assert (pages[P1].status, pages[P1].run_id) == (PageStatus.ACTIVE, run.id)
    assert pages[P1].fetch_requested_at is not None
    assert (pages[P3].status, pages[P3].run_id) == (PageStatus.PENDING, run.id)
    assert pages[P2].fetch_requested_at is None  # archived pages aren't fetched
    await db_session.refresh(sitemap)
    assert about(sitemap.next_check_at, started + timedelta(days=1))
    assert about(sitemap.last_checked_at, started)
    assert sitemap.entry_count == 2


async def test_an_unreadable_sitemap_is_tried_again_in_an_hour(db_session: AsyncSession) -> None:
    sitemap = await crawled(db_session, {SITEMAP: urlset(P1, P2)})
    last_checked = sitemap.last_checked_at

    run = await claim_and_check(db_session, {SITEMAP: 503})

    assert run is None
    await db_session.refresh(sitemap)
    assert sitemap.last_error == "The server answered HTTP 503"
    # It keeps its counts, and the dashboard's last sync stays at the last good check
    assert (sitemap.entry_count, sitemap.last_checked_at) == (2, last_checked)
    assert about(sitemap.next_check_at, datetime.now(UTC) + RETRY_AFTER)
    queued = await db_session.scalar(
        select(func.count()).select_from(Page).where(Page.fetch_requested_at.is_not(None))
    )
    assert queued == 0


async def test_a_broken_sitemap_inside_an_index_keeps_its_counts_and_pages(
    db_session: AsyncSession,
) -> None:
    a, b = f"{SITE}/a.xml", f"{SITE}/b.xml"
    await crawled(db_session, {SITEMAP: index(a, b), a: urlset(P1, P2), b: urlset(P3)})

    run = await claim_and_check(db_session, {SITEMAP: index(a, b), a: urlset(P1, P2), b: 500})

    assert run is not None and run.total_pages == 3  # b's page is fetched again all the same
    broken = (
        await db_session.scalars(
            select(Sitemap).where(Sitemap.url == b).execution_options(populate_existing=True)
        )
    ).one()
    assert (broken.last_error, broken.entry_count) == ("The server answered HTTP 500", 1)


async def test_no_check_while_the_last_crawl_is_still_running(db_session: AsyncSession) -> None:
    sitemap = await crawled(db_session, {SITEMAP: urlset(P1, P2)})
    await db_session.execute(
        update(Page).where(Page.url == P2).values(fetch_requested_at=datetime.now(UTC))
    )
    await db_session.commit()

    run = await claim_and_check(db_session, {SITEMAP: urlset(P1, P2, P3)})

    assert run is None
    assert P3 not in await pages_by_url(db_session)
    await db_session.refresh(sitemap)
    assert about(sitemap.next_check_at, datetime.now(UTC) + RETRY_AFTER)


async def test_check_now(session_factory: async_sessionmaker[AsyncSession]) -> None:
    settings = {"schedule": Schedule.DAILY, "retention": Retention.AFTER_2}
    async with session_factory() as session:
        sitemap = await crawled(session, {SITEMAP: urlset(P1, P2)})
        sitemap.next_check_at = datetime.now(UTC) + timedelta(days=1)
        child = Sitemap(url=f"{SITE}/child.xml", parent_id=sitemap.id, **settings)
        unconfirmed = Sitemap(url=f"{SITE}/new.xml", **settings)
        session.add_all([child, unconfirmed])
        await session.execute(
            update(Page).where(Page.url == P2).values(fetch_requested_at=datetime.now(UTC))
        )
        await session.commit()

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            running = await api.post(f"/v1/sitemaps/{sitemap.id}/check")
            assert (running.status_code, running.json()["detail"]) == (
                409,
                "Its last crawl is still running; pages queued: 1",
            )
            async with session_factory() as session:
                await session.execute(update(Page).values(fetch_requested_at=None))
                await session.commit()

            checked = await api.post(f"/v1/sitemaps/{sitemap.id}/check")
            assert checked.status_code == 202
            assert about(datetime.fromisoformat(checked.json()["next_check_at"]), datetime.now(UTC))

            assert (await api.post("/v1/sitemaps/9999/check")).status_code == 404
            assert (await api.post(f"/v1/sitemaps/{child.id}/check")).status_code == 409
            assert (await api.post(f"/v1/sitemaps/{unconfirmed.id}/check")).status_code == 409
    finally:
        app.dependency_overrides.clear()

    async with session_factory() as session:  # the worker picks it up at its next look
        claimed = await claim_due_sitemap(session)
    assert claimed is not None and claimed.id == sitemap.id
