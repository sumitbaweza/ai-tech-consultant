"""The crawl worker against the test database, with a fake fetcher instead of the network."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.fetch import FetchBlocked, Fetched, FetchError
from app.jobs.worker import claim_next_page, process_page
from app.models import (
    Image,
    IngestionRun,
    Page,
    PageKind,
    PageStatus,
    PageVersion,
    RunKind,
    RunStatus,
)

pytestmark = pytest.mark.db  # every test here needs Postgres

ARTICLE = (Path(__file__).parent / "fixtures" / "freshdesk_article.html").read_bytes()
URL = "https://help.example/support/solutions/articles/1-example-labels"


class FakeFetcher:
    """Returns (or raises) the given results in order, and records each request."""

    def __init__(self, *results: Fetched | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Fetched:
        self.calls.append((url, etag, last_modified))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def html(body: bytes = ARTICLE) -> Fetched:
    return Fetched(url=URL, status=200, body=body, content_type="text/html", etag='"v1"')


async def queued_page(session: AsyncSession) -> Page:
    """One queued page in a running ingestion, as POST /v1/ingestions leaves it."""
    run = IngestionRun(
        kind=RunKind.INITIAL, status=RunStatus.RUNNING, sitemap_ids=[], total_pages=1
    )
    session.add(run)
    await session.flush()
    page = Page(
        url=URL,
        kind=PageKind.ARTICLE,
        status=PageStatus.PENDING,
        fetch_requested_at=datetime.now(UTC),
        run_id=run.id,
    )
    session.add(page)
    await session.commit()
    return page


async def queue_again(session: AsyncSession, page: Page) -> None:
    """What a scheduled re-check does: put the page back in the queue."""
    page.fetch_requested_at = datetime.now(UTC)
    await session.commit()


def queued(url: str, requested_minutes_ago: int, **values: Any) -> Page:
    return Page(
        url=url,
        kind=PageKind.ARTICLE,
        status=PageStatus.PENDING,
        fetch_requested_at=datetime.now(UTC) - timedelta(minutes=requested_minutes_ago),
        **values,
    )


async def test_claims_skip_live_workers_pages_and_take_over_stale_ones(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            queued(f"{URL}-live", 3, claimed_at=now),  # a worker is on it right now
            queued(f"{URL}-stale", 2, claimed_at=now - timedelta(minutes=10)),  # its worker died
            queued(f"{URL}-new", 1),
            Page(url=f"{URL}-done", kind=PageKind.ARTICLE, status=PageStatus.ACTIVE),
        ]
    )
    await db_session.commit()

    claimed = [await claim_next_page(db_session) for _ in range(3)]

    assert [page.url if page else None for page in claimed] == [f"{URL}-stale", f"{URL}-new", None]


async def test_new_article_becomes_active_with_a_version_and_its_screenshots(
    db_session: AsyncSession,
) -> None:
    page = await queued_page(db_session)

    await process_page(db_session, FakeFetcher(html()), page)

    assert page.status == PageStatus.ACTIVE
    assert (page.title, page.category, page.folder) == (
        "Settings » Example Labels",
        "Settings",
        "Example Labels",
    )
    assert page.content_hash and page.etag == '"v1"'
    assert (page.fetch_requested_at, page.claimed_at) == (None, None)
    assert await db_session.scalar(select(func.count()).select_from(PageVersion)) == 1
    images = (await db_session.scalars(select(Image).order_by(Image.position))).all()
    assert [image.alt for image in images] == ["The label list with a search box", None]
    run = await db_session.get(IngestionRun, page.run_id, populate_existing=True)
    assert run is not None and run.status == RunStatus.DONE  # its only page is done


async def test_same_content_again_adds_no_version_and_sends_the_etag(
    db_session: AsyncSession,
) -> None:
    page = await queued_page(db_session)
    fetcher = FakeFetcher(html(), html())

    await process_page(db_session, fetcher, page)
    await queue_again(db_session, page)
    await process_page(db_session, fetcher, page)

    assert await db_session.scalar(select(func.count()).select_from(PageVersion)) == 1
    assert fetcher.calls[1] == (URL, '"v1"', None)


async def test_a_new_screenshot_keeps_the_other_images_descriptions(
    db_session: AsyncSession,
) -> None:
    page = await queued_page(db_session)
    await process_page(db_session, FakeFetcher(html()), page)
    first = (await db_session.scalars(select(Image).where(Image.position == 1))).one()
    first.description, first.sha256 = "A list of text labels", "a" * 64
    await db_session.commit()

    # Screenshot 2 is replaced; screenshot 1 only gets a new cache-buster
    changed = ARTICLE.replace(b"222/edit-window", b"444/new-window").replace(
        b"?1700000000", b"?1800000000"
    )
    await queue_again(db_session, page)
    await process_page(db_session, FakeFetcher(html(changed)), page)

    images = (await db_session.scalars(select(Image).order_by(Image.position))).all()
    assert [(image.description, image.sha256) for image in images] == [
        ("A list of text labels", "a" * 64),
        (None, None),
    ]
    assert await db_session.scalar(select(func.count()).select_from(PageVersion)) == 2


async def test_304_keeps_the_content(db_session: AsyncSession) -> None:
    page = await queued_page(db_session)
    await process_page(db_session, FakeFetcher(html()), page)
    hash_before = page.content_hash

    await queue_again(db_session, page)
    await process_page(db_session, FakeFetcher(Fetched(url=URL, status=304)), page)

    assert (page.status, page.http_status, page.content_hash) == (
        PageStatus.ACTIVE,
        304,
        hash_before,
    )


async def test_permanent_redirect_queues_the_new_address(db_session: AsyncSession) -> None:
    page = await queued_page(db_session)
    new_url = "https://help.example/support/solutions/articles/1-renamed-labels"

    await process_page(
        db_session, FakeFetcher(Fetched(url=URL, status=301, location=new_url)), page
    )

    target = (await db_session.scalars(select(Page).where(Page.url == new_url))).one()
    assert (page.status, page.redirected_to_id) == (PageStatus.REDIRECTED, target.id)
    assert target.status == PageStatus.PENDING and target.fetch_requested_at is not None
    run = await db_session.get(IngestionRun, page.run_id, populate_existing=True)
    assert run is not None and (run.total_pages, run.status) == (2, RunStatus.RUNNING)


async def test_404_marks_the_page_not_found(db_session: AsyncSession) -> None:
    page = await queued_page(db_session)

    await process_page(db_session, FakeFetcher(Fetched(url=URL, status=404)), page)

    assert page.status == PageStatus.NOT_FOUND


async def test_failures_are_retried_then_the_page_is_marked_failed(
    db_session: AsyncSession,
) -> None:
    page = await queued_page(db_session)
    fetcher = FakeFetcher(*(FetchError("HTTP 503 after 5 tries") for _ in range(3)))

    for attempts in (1, 2):
        await process_page(db_session, fetcher, page)
        assert (page.status, page.attempts) == (PageStatus.PENDING, attempts)
        assert page.fetch_requested_at is not None  # still in the queue

    await process_page(db_session, fetcher, page)

    assert page.status == PageStatus.FAILED
    assert "3 attempts" in (page.error or "")


async def test_a_robots_txt_block_fails_at_once(db_session: AsyncSession) -> None:
    page = await queued_page(db_session)

    await process_page(db_session, FakeFetcher(FetchBlocked("robots.txt says no")), page)

    assert (page.status, page.error) == (PageStatus.FAILED, "robots.txt says no")
