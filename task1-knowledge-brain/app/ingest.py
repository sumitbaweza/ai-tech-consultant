"""Save what discovery found, show the preview, and confirm an ingestion.

Nothing is fetched before the user confirms: discovery stores pages as `discovered`, and
confirming turns them into `pending` (queued for the worker) under a new ingestion run.
"""

from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import CursorResult, distinct, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.sitemap import FoundSitemap, guess_kind
from app.models import (
    IngestionRun,
    Page,
    PageSitemap,
    PageStatus,
    Retention,
    RunKind,
    RunStatus,
    Schedule,
    Sitemap,
)

# Rows per INSERT. Postgres takes at most 32,767 parameters in one statement.
BATCH_SIZE = 1000
# The preview lists at most this many duplicate URLs; the count covers all of them
MAX_DUPLICATES_SHOWN = 100


class SitemapSummary(BaseModel):
    id: int
    url: str
    parent_id: int | None
    is_index: bool
    # <url> entries (or <sitemap> entries, for an index) in the file, duplicates included
    entries: int
    first_url: str | None
    last_url: str | None
    error: str | None


class DuplicateUrl(BaseModel):
    url: str
    sitemaps: list[str]  # every sitemap that lists it


class Estimate(BaseModel):
    pages_per_minute: float
    pages_per_hour: float
    pages_per_day: float
    minutes_to_finish: float
    expected_completion: datetime


class Preview(BaseModel):
    """What the user sees before confirming: test 2's pre-processing summary."""

    # Pass these to POST /v1/ingestions to confirm
    sitemap_ids: list[int]
    sitemaps: list[SitemapSummary]
    total_sitemaps: int
    total_urls: int  # every <url> entry, duplicates included
    unique_urls: int
    duplicate_urls: int  # total_urls - unique_urls: entries that won't be fetched twice
    duplicates: list[DuplicateUrl]  # URLs listed by more than one sitemap
    new_urls: int  # not in the knowledge base yet: what confirming will fetch
    estimate: Estimate


def estimate(pages: int, pages_per_minute: float, now: datetime) -> Estimate:
    """How long `pages` take at a given speed. Before the crawl that speed is the polite rate
    limit (the crawl can't go faster); during it, the measured speed."""
    minutes = pages / pages_per_minute if pages_per_minute > 0 else 0.0
    return Estimate(
        pages_per_minute=round(pages_per_minute, 1),
        pages_per_hour=round(pages_per_minute * 60),
        pages_per_day=round(pages_per_minute * 60 * 24),
        minutes_to_finish=round(minutes, 1),
        expected_completion=now + timedelta(minutes=minutes),
    )


async def save_discovery(
    session: AsyncSession, found: list[FoundSitemap], schedule: Schedule, retention: Retention
) -> list[int]:
    """Store the sitemaps, their pages, and which sitemap lists which page.

    Returns the ids of the sitemaps the user added, which preview and confirm take. Pages
    already in the knowledge base keep their status; new ones start as `discovered`.
    """
    now = datetime.now(UTC)

    # Sitemaps, parents first: discovery is breadth first, so a parent is always saved
    # before its children and its id is known.
    sitemap_ids: dict[str, int] = {}
    for sitemap in found:
        values: dict[str, Any] = {
            "parent_id": sitemap_ids.get(sitemap.parent_url or ""),
            "schedule": schedule,
            "retention": retention,
            "last_error": sitemap.error,
        }
        if sitemap.was_read:
            # A sitemap that couldn't be read keeps the counts from the last time it was, so
            # one failed fetch during a scheduled check doesn't empty the totals
            values |= {
                "is_index": sitemap.is_index,
                "entry_count": len(sitemap.children if sitemap.is_index else sitemap.pages),
                "last_checked_at": now,
            }
        upsert = (
            insert(Sitemap)
            .values(url=sitemap.url, **values)
            .on_conflict_do_update(index_elements=[Sitemap.url], set_=values)
            .returning(Sitemap.id)
        )
        sitemap_ids[sitemap.url] = (await session.execute(upsert)).scalar_one()

    # Pages: each URL once, even if several sitemaps list it, because Postgres can't upsert
    # the same row twice in one statement
    lastmods: dict[str, datetime | None] = {}
    for sitemap in found:
        for page in sitemap.pages:
            lastmods.setdefault(page.url, page.lastmod)
    page_rows = [
        {
            "url": url,
            "kind": guess_kind(url),
            "status": PageStatus.DISCOVERED,
            "sitemap_lastmod": lastmod,
        }
        for url, lastmod in lastmods.items()
    ]
    page_ids: dict[str, int] = {}
    for batch in _batches(page_rows):
        upsert = insert(Page).values(batch)
        # On conflict, only refresh lastmod: a page already in the knowledge base keeps its
        # status and content. DO UPDATE (unlike DO NOTHING) returns existing rows' ids too.
        upsert = upsert.on_conflict_do_update(
            index_elements=[Page.url],
            set_={"sitemap_lastmod": upsert.excluded.sitemap_lastmod},
        ).returning(Page.id, Page.url)
        for page_id, url in await session.execute(upsert):
            page_ids[url] = page_id

    # Which sitemap lists which page, at the position of its first appearance in the file
    for sitemap in found:
        positions: dict[int, int] = {}
        for position, page in enumerate(sitemap.pages):
            positions.setdefault(page_ids[page.url], position)
        reference_rows = [
            {"page_id": page_id, "sitemap_id": sitemap_ids[sitemap.url], "position": position}
            for page_id, position in positions.items()
        ]
        for batch in _batches(reference_rows):
            upsert = insert(PageSitemap).values(batch)
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[PageSitemap.page_id, PageSitemap.sitemap_id],
                    set_={"position": upsert.excluded.position, "last_seen_at": func.now()},
                )
            )

    await session.commit()
    return [sitemap_ids[sitemap.url] for sitemap in found if sitemap.parent_url is None]


async def build_preview(
    session: AsyncSession, root_ids: list[int], requests_per_second: float
) -> Preview:
    """The summary for the sitemaps the user added and every sitemap inside them."""
    sitemaps = await sitemap_tree(session, root_ids)
    ids = [sitemap.id for sitemap in sitemaps]
    first_urls, last_urls = await _first_and_last_urls(session, ids)

    listed = PageSitemap.sitemap_id.in_(ids)
    unique_urls = (
        await session.scalar(select(func.count(distinct(PageSitemap.page_id))).where(listed)) or 0
    )
    new_urls = (
        await session.scalar(
            select(func.count(distinct(Page.id)))
            .join(PageSitemap, PageSitemap.page_id == Page.id)
            .where(listed, Page.status == PageStatus.DISCOVERED)
        )
        or 0
    )
    duplicate_rows = await session.execute(
        select(Page.url, func.array_agg(Sitemap.url))
        .join(PageSitemap, PageSitemap.page_id == Page.id)
        .join(Sitemap, Sitemap.id == PageSitemap.sitemap_id)
        .where(listed)
        .group_by(Page.url)
        .having(func.count() > 1)
        .order_by(Page.url)
        .limit(MAX_DUPLICATES_SHOWN)
    )
    total_urls = sum(sitemap.entry_count for sitemap in sitemaps if not sitemap.is_index)

    return Preview(
        sitemap_ids=root_ids,
        sitemaps=[
            SitemapSummary(
                id=sitemap.id,
                url=sitemap.url,
                parent_id=sitemap.parent_id,
                is_index=sitemap.is_index,
                entries=sitemap.entry_count,
                first_url=first_urls.get(sitemap.id),
                last_url=last_urls.get(sitemap.id),
                error=sitemap.last_error,
            )
            for sitemap in sitemaps
        ],
        total_sitemaps=len(sitemaps),
        total_urls=total_urls,
        unique_urls=unique_urls,
        duplicate_urls=total_urls - unique_urls,
        duplicates=[
            DuplicateUrl(url=url, sitemaps=sorted(cast(list[str], urls)))
            for url, urls in duplicate_rows
        ],
        new_urls=new_urls,
        estimate=estimate(new_urls, requests_per_second * 60, datetime.now(UTC)),
    )


async def confirm_ingestion(session: AsyncSession, root_ids: list[int]) -> IngestionRun:
    """The user said yes: queue every discovered page of these sitemaps for the worker, and
    start each sitemap's re-check schedule."""
    sitemaps = await sitemap_tree(session, root_ids)
    run = IngestionRun(kind=RunKind.INITIAL, status=RunStatus.RUNNING, sitemap_ids=root_ids)
    session.add(run)
    await session.flush()  # gives the run its id

    listed_pages = select(PageSitemap.page_id).where(
        PageSitemap.sitemap_id.in_([sitemap.id for sitemap in sitemaps])
    )
    queued = await session.execute(
        update(Page)
        .where(Page.id.in_(listed_pages), Page.status == PageStatus.DISCOVERED)
        .values(status=PageStatus.PENDING, fetch_requested_at=func.now(), run_id=run.id)
        .execution_options(synchronize_session=False)
    )
    run.total_pages = cast(CursorResult[Any], queued).rowcount
    now = datetime.now(UTC)
    if run.total_pages == 0:  # everything was already in the knowledge base
        run.status = RunStatus.DONE
        run.finished_at = now

    for sitemap in sitemaps:
        if sitemap.id in root_ids:
            sitemap.next_check_at = now + sitemap.schedule.interval
    await session.commit()
    return run


async def sitemap_tree(session: AsyncSession, root_ids: list[int]) -> list[Sitemap]:
    """The given sitemaps and all the sitemaps inside them, parents before children.

    Raises LookupError if an id doesn't exist.
    """
    everything = (await session.scalars(select(Sitemap).order_by(Sitemap.id))).all()
    by_id = {sitemap.id: sitemap for sitemap in everything}
    missing = [sitemap_id for sitemap_id in root_ids if sitemap_id not in by_id]
    if missing:
        raise LookupError(f"No sitemap with id {', '.join(map(str, missing))}")

    children: dict[int, list[Sitemap]] = defaultdict(list)
    for sitemap in everything:
        if sitemap.parent_id is not None:
            children[sitemap.parent_id].append(sitemap)
    tree: list[Sitemap] = []
    queue = [by_id[sitemap_id] for sitemap_id in root_ids]
    while queue:
        sitemap = queue.pop(0)
        if sitemap in tree:
            continue  # the same sitemap reached twice
        tree.append(sitemap)
        queue.extend(children[sitemap.id])
    return tree


async def _first_and_last_urls(
    session: AsyncSession, sitemap_ids: list[int]
) -> tuple[dict[int, str], dict[int, str]]:
    """Each sitemap's first and last URL, by position in the file."""
    ranked = (
        select(
            PageSitemap.sitemap_id,
            Page.url,
            func.row_number()
            .over(partition_by=PageSitemap.sitemap_id, order_by=PageSitemap.position)
            .label("from_start"),
            func.row_number()
            .over(partition_by=PageSitemap.sitemap_id, order_by=PageSitemap.position.desc())
            .label("from_end"),
        )
        .join(Page, Page.id == PageSitemap.page_id)
        .where(PageSitemap.sitemap_id.in_(sitemap_ids))
        .subquery()
    )
    rows = await session.execute(
        select(ranked.c.sitemap_id, ranked.c.url, ranked.c.from_start, ranked.c.from_end).where(
            or_(ranked.c.from_start == 1, ranked.c.from_end == 1)
        )
    )
    first: dict[int, str] = {}
    last: dict[int, str] = {}
    for sitemap_id, url, from_start, from_end in rows:
        if from_start == 1:
            first[sitemap_id] = url
        if from_end == 1:  # a one-URL sitemap is both
            last[sitemap_id] = url
    return first, last


def _batches(rows: Sequence[dict[str, Any]]) -> Iterator[Sequence[dict[str, Any]]]:
    for start in range(0, len(rows), BATCH_SIZE):
        yield rows[start : start + BATCH_SIZE]
