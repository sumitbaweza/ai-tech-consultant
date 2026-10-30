"""The crawl worker: takes queued pages from Postgres and fetches them politely.

The brain-worker container runs `python -m app.jobs.worker`. Its CONCURRENCY tasks share one
HTTP client, one rate limiter and one robots.txt cache, so together they still send at most
one request per second to the help center.

The queue is the pages table itself. A page is queued while fetch_requested_at is set, and a
worker claims it by stamping claimed_at. A claim older than STALE_CLAIM means its worker died,
so another worker takes the page over. That is how a stopped crawl resumes: start the worker
again, and it carries on with whatever is still queued.
"""

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.crawler.fetch import FetchBlocked, Fetched, Fetcher, FetchError
from app.crawler.http import make_client
from app.crawler.parse import ImageRef, parse_page, without_query
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import guess_kind
from app.db import SessionFactory
from app.models import Image, IngestionRun, Page, PageKind, PageStatus, PageVersion, RunStatus

logger = logging.getLogger("brain.worker")

CONCURRENCY = 2
STALE_CLAIM = timedelta(minutes=5)
MAX_ATTEMPTS = 3  # failed fetches in a row before the worker gives up on a page
IDLE_SECONDS = 5.0  # how long to wait before looking again when the queue is empty


class PageFetcher(Protocol):
    async def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Fetched: ...


async def claim_next_page(session: AsyncSession) -> Page | None:
    """Claim the oldest queued page that no live worker holds.

    FOR UPDATE SKIP LOCKED lets two workers claim at the same moment without waiting for each
    other and without taking the same page.
    """
    next_page = (
        select(Page.id)
        .where(
            Page.fetch_requested_at.is_not(None),
            or_(Page.claimed_at.is_(None), Page.claimed_at < func.now() - STALE_CLAIM),
        )
        .order_by(Page.fetch_requested_at, Page.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.scalars(
        update(Page).where(Page.id == next_page).values(claimed_at=func.now()).returning(Page)
    )
    page = claimed.one_or_none()
    await session.commit()
    return page


async def process_page(session: AsyncSession, fetcher: PageFetcher, page: Page) -> None:
    """Fetch one claimed page and record what happened. Every outcome releases the claim."""
    try:
        fetched = await fetcher.get(page.url, etag=page.etag, last_modified=page.last_modified)
    except FetchBlocked as error:
        _give_up(page, str(error))
    except FetchError as error:
        page.attempts += 1
        if page.attempts >= MAX_ATTEMPTS:
            _give_up(page, f"{error} ({page.attempts} attempts)")
        else:
            page.error = str(error)
            page.claimed_at = None
            page.fetch_requested_at = datetime.now(UTC)  # to the back of the queue
    else:
        await _record(session, page, fetched)
    await session.commit()
    if page.run_id is not None:
        await _finish_run_if_done(session, page.run_id)


async def _record(session: AsyncSession, page: Page, fetched: Fetched) -> None:
    page.http_status = fetched.status
    match fetched.status:
        case 200 if fetched.content_type.startswith("text/html"):
            await _save_content(session, page, fetched)
        case 200:
            _give_up(page, f"Not an HTML page ({fetched.content_type or 'no content type'})")
        case 304:  # unchanged since the last fetch
            page.status = PageStatus.ACTIVE
            page.fetched_at = datetime.now(UTC)
            _done(page)
        case 301 | 308 if fetched.location:
            await _record_move(session, page, fetched.location)
        case 404 | 410:
            page.status = PageStatus.NOT_FOUND
            _done(page)
        case status:
            _give_up(page, f"The server answered HTTP {status}")


async def _save_content(session: AsyncSession, page: Page, fetched: Fetched) -> None:
    parsed = parse_page(fetched.body, page.url)
    # A URL that looks like a listing stays one; an article page without an article body
    # (nothing to embed) becomes one
    if page.kind == PageKind.ARTICLE and not parsed.is_article:
        page.kind = PageKind.LISTING
    page.title, page.category, page.folder = parsed.title, parsed.category, parsed.folder
    page.etag, page.last_modified = fetched.etag, fetched.last_modified

    if parsed.content_hash != page.content_hash:
        page.content_md = parsed.markdown
        page.content_hash = parsed.content_hash
        session.add(
            PageVersion(
                page_id=page.id,
                content_hash=parsed.content_hash,
                title=parsed.title,
                content_md=parsed.markdown,
            )
        )
        await _replace_images(session, page.id, parsed.images)

    page.status = PageStatus.ACTIVE
    page.fetched_at = datetime.now(UTC)
    _done(page)


async def _replace_images(session: AsyncSession, page_id: int, images: list[ImageRef]) -> None:
    """Replace the page's image rows, carrying over the hash and description of each image
    that stayed, so an unchanged screenshot is never described (or paid for) twice."""
    old_rows = await session.scalars(select(Image).where(Image.page_id == page_id))
    known = {
        without_query(row.url): (row.sha256, row.description, row.described_by) for row in old_rows
    }
    await session.execute(delete(Image).where(Image.page_id == page_id))
    for image in images:
        sha256, description, described_by = known.get(without_query(image.url), (None, None, None))
        session.add(
            Image(
                page_id=page_id,
                position=image.position,
                url=image.url,
                alt=image.alt,
                context=image.context,
                sha256=sha256,
                description=description,
                described_by=described_by,
            )
        )


async def _record_move(session: AsyncSession, page: Page, new_url: str) -> None:
    """A permanent redirect: the old page leaves search, and the new address is queued."""
    target_id = await session.scalar(select(Page.id).where(Page.url == new_url))
    if target_id is None:
        target = Page(
            url=new_url,
            kind=guess_kind(new_url),
            status=PageStatus.PENDING,
            fetch_requested_at=datetime.now(UTC),
            run_id=page.run_id,
        )
        session.add(target)
        await session.flush()  # gives the new page its id
        target_id = target.id
        if page.run_id is not None:  # the run now has one more page to fetch
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.id == page.run_id)
                .values(total_pages=IngestionRun.total_pages + 1)
            )
    page.redirected_to_id = target_id
    page.status = PageStatus.REDIRECTED
    _done(page)


async def _finish_run_if_done(session: AsyncSession, run_id: int) -> None:
    remaining = await session.scalar(
        select(func.count())
        .select_from(Page)
        .where(Page.run_id == run_id, Page.fetch_requested_at.is_not(None))
    )
    if remaining == 0:
        await session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id, IngestionRun.status == RunStatus.RUNNING)
            .values(status=RunStatus.DONE, finished_at=func.now())
        )
        await session.commit()


def _done(page: Page) -> None:
    """Take the page out of the queue and clear its failure count."""
    page.fetch_requested_at = None
    page.claimed_at = None
    page.attempts = 0
    page.error = None


def _give_up(page: Page, error: str) -> None:
    """Stop trying. A page that already has content keeps it and stays searchable (stale beats
    missing); a page that was never fetched is marked failed."""
    _done(page)
    page.error = error
    if page.content_hash is None:
        page.status = PageStatus.FAILED


# --- Running the worker ---------------------------------------------------------------------


async def crawl(
    session_factory: async_sessionmaker[AsyncSession], fetcher: PageFetcher, stop: asyncio.Event
) -> None:
    """One worker task: claim a page, fetch it, repeat until told to stop."""
    while not stop.is_set():
        async with session_factory() as session:
            page = await claim_next_page(session)
            if page is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), IDLE_SECONDS)
                continue
            try:
                await process_page(session, fetcher, page)
                logger.info("%s %s -> %s", page.http_status, page.url, page.status)
            except Exception:
                # One bad page mustn't stop the crawl. Its claim expires, so it's retried later.
                logger.exception("Failed on %s", page.url)
                await session.rollback()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()

    # Docker stops a container with SIGTERM: finish the current pages, then exit
    stop = asyncio.Event()
    with contextlib.suppress(NotImplementedError):  # signal handlers don't exist on Windows
        for stop_signal in (signal.SIGTERM, signal.SIGINT):
            asyncio.get_running_loop().add_signal_handler(stop_signal, stop.set)

    async with make_client(settings) as client:
        limiter = HostRateLimiter(settings.crawler_requests_per_second)
        robots = RobotsRules(client, settings.crawler_user_agent, limiter)
        fetcher = Fetcher(client, limiter, robots)
        logger.info(
            "Worker started: %d tasks, at most %.1f requests per second per host",
            CONCURRENCY,
            settings.crawler_requests_per_second,
        )
        await asyncio.gather(*(crawl(SessionFactory, fetcher, stop) for _ in range(CONCURRENCY)))
    logger.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
