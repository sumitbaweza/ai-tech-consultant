"""Numbers for the live progress stream and for the dashboard (test 2's list)."""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest import Estimate, estimate
from app.models import IngestionRun, Page, PageSitemap, PageStatus, RunStatus, Sitemap

# Speed is measured over the last few minutes, so the estimate follows the real pace
SPEED_WINDOW = timedelta(minutes=5)


class Progress(BaseModel):
    run_id: int
    status: RunStatus
    total: int  # pages this run queued
    done: int  # fetched, or given up on
    remaining: int
    by_status: dict[str, int]  # where this run's pages are now
    pages_per_minute: float  # measured over the last 5 minutes
    estimate: Estimate | None  # time left at that speed; None while there's no speed yet
    started_at: datetime
    finished_at: datetime | None


class Stats(BaseModel):
    """Test 2's dashboard, for the whole knowledge base."""

    total_pages: int
    processed: int  # handled by the crawler: fetched, moved, gone or failed
    embedded: int  # searchable: active pages whose chunks match their current content
    failed: int
    pending: int  # queued for their first fetch
    duplicates_skipped: int  # sitemap entries for a URL already listed, never fetched twice
    archived: int
    last_sync: datetime | None
    by_status: dict[str, int]


async def run_progress(session: AsyncSession, run_id: int) -> Progress:
    run = await session.get(IngestionRun, run_id)
    if run is None:
        raise LookupError(f"No ingestion with id {run_id}")

    in_run = Page.run_id == run_id
    status_rows = await session.execute(
        select(Page.status, func.count()).where(in_run).group_by(Page.status)
    )
    by_status = {status.value: count for status, count in status_rows}
    remaining = (
        await session.scalar(
            select(func.count())
            .select_from(Page)
            .where(in_run, Page.fetch_requested_at.is_not(None))
        )
        or 0
    )

    now = datetime.now(UTC)
    window_start = max(run.started_at, now - SPEED_WINDOW)
    minutes = max((now - window_start).total_seconds() / 60, 1 / 60)  # at least a second
    recently_fetched = (
        await session.scalar(
            select(func.count()).select_from(Page).where(in_run, Page.fetched_at >= window_start)
        )
        or 0
    )
    pages_per_minute = recently_fetched / minutes

    return Progress(
        run_id=run.id,
        status=run.status,
        total=run.total_pages,
        done=max(run.total_pages - remaining, 0),
        remaining=remaining,
        by_status=by_status,
        pages_per_minute=round(pages_per_minute, 1),
        estimate=estimate(remaining, pages_per_minute, now)
        if remaining and pages_per_minute > 0
        else None,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def knowledge_base_stats(session: AsyncSession) -> Stats:
    status_rows = await session.execute(select(Page.status, func.count()).group_by(Page.status))
    by_status = {status.value: count for status, count in status_rows}
    pending = by_status.get(PageStatus.PENDING.value, 0)
    discovered = by_status.get(PageStatus.DISCOVERED.value, 0)
    total = sum(by_status.values())

    embedded = (
        await session.scalar(
            select(func.count())
            .select_from(Page)
            .where(Page.status == PageStatus.ACTIVE, Page.embedded_hash == Page.content_hash)
        )
        or 0
    )
    # Every <url> entry across the sitemaps, minus the distinct pages they list
    entries = (
        await session.scalar(
            select(func.coalesce(func.sum(Sitemap.entry_count), 0)).where(
                Sitemap.is_index.is_(False)
            )
        )
        or 0
    )
    listed_pages = (
        await session.scalar(
            select(func.count(distinct(PageSitemap.page_id))).where(
                PageSitemap.removed_at.is_(None)
            )
        )
        or 0
    )
    last_fetch = await session.scalar(select(func.max(Page.fetched_at)))
    last_check = await session.scalar(select(func.max(Sitemap.last_checked_at)))

    return Stats(
        total_pages=total,
        processed=total - pending - discovered,
        embedded=embedded,
        failed=by_status.get(PageStatus.FAILED.value, 0),
        pending=pending,
        duplicates_skipped=entries - listed_pages,
        archived=by_status.get(PageStatus.ARCHIVED.value, 0),
        last_sync=max((time for time in (last_fetch, last_check) if time), default=None),
        by_status=by_status,
    )
