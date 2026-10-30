"""Scheduled sitemap checks: keep the knowledge base in step with the help center.

Confirming a sitemap's first crawl sets its next_check_at from its schedule (daily, weekly,
every 15 or 30 days). Every minute the worker's check task claims a sitemap whose time has come
and checks it:

1. Read the sitemap and the sitemaps inside it again, with the same polite discovery code as
   the first time. Discovery marks the URLs that a sitemap no longer lists.
2. Apply the retention rule. A page that no sitemap lists any more has missed one more check,
   and once it has missed as many in a row as the rule says (1, 2, 15 or 30), it's archived:
   kept with its text, history, screenshots and vectors, but out of search. A page listed
   again starts from 0, and an archived one is fetched again, which makes it active.
3. Add new URLs to the knowledge base and queue them at once. The user already confirmed this
   sitemap and its schedule, so there's no second preview.
4. Queue every other page of the sitemap for another fetch, except archived ones. That
   includes missing pages the rule still keeps, so a deleted article that answers 404 leaves
   search at once. The content hash decides what changed: an unchanged page costs one download
   (or a 304, from a server that sends ETag or Last-Modified), and only a changed page gets a
   change-history row, new screenshot descriptions and new embeddings.
5. Set the next check to this check's start plus the schedule's interval. After downtime that
   gives one catch-up check, not one per missed day.

The queued pages belong to a new `refresh` ingestion run, so the progress stream and its ETA
work as they do for the first crawl.

A check is put off for RETRY_AFTER when the sitemap can't be read (its error is saved on it),
or when its last crawl is still running, since queueing those pages again would fetch them
twice. A broken sitemap inside it doesn't stop the check, and its URLs don't count as missing.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, cast

import httpx2
from sqlalchemy import ColumnElement, CursorResult, Result, distinct, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import discover
from app.ingest import save_discovery, sitemap_tree
from app.models import (
    IngestionRun,
    Page,
    PageSitemap,
    PageStatus,
    Retention,
    RunKind,
    RunStatus,
    Sitemap,
)

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(hours=1)


class Retained(NamedTuple):
    """What the retention rule did in one check."""

    missing: int  # pages that no sitemap lists any more: each missed one more check
    archived: int  # of those, the ones that reached the rule in this check
    restored: int  # archived pages listed again, queued to be fetched and made active


async def claim_due_sitemap(session: AsyncSession) -> Sitemap | None:
    """Claim the most overdue sitemap the user added. The sitemaps inside it are checked with
    it, so they're never claimed on their own.

    The claim moves next_check_at RETRY_AFTER ahead in the same statement, so no other worker
    takes the sitemap too, and a check that dies halfway runs again later instead of at once,
    over and over.
    """
    due = (
        select(Sitemap.id)
        .where(Sitemap.parent_id.is_(None), Sitemap.next_check_at <= func.now())
        .order_by(Sitemap.next_check_at, Sitemap.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = await session.scalars(
        update(Sitemap)
        .where(Sitemap.id == due)
        .values(next_check_at=func.now() + RETRY_AFTER)
        .returning(Sitemap)
        # If this sitemap is already loaded in the session, overwrite it with what was returned
        .execution_options(populate_existing=True)
    )
    sitemap = claimed.one_or_none()
    await session.commit()
    return sitemap


async def check_sitemap(
    session: AsyncSession,
    sitemap: Sitemap,
    client: httpx2.AsyncClient,
    robots: RobotsRules,
    limiter: HostRateLimiter,
) -> IngestionRun | None:
    """Check a claimed sitemap. Returns the refresh run that fetches its pages again, or None
    when the check is put off (the claim already made it due again in RETRY_AFTER)."""
    started = datetime.now(UTC)
    if queued := await pages_still_queued(session, sitemap.id):
        logger.info("Not checking %s yet: its last crawl has %d pages queued", sitemap.url, queued)
        return None

    found = await discover([sitemap.url], client, robots, limiter)
    # Without the sitemap itself there's nothing to compare. A broken sitemap inside it doesn't
    # stop the check: it keeps its error and its pages, and the others carry on.
    if not found[0].was_read:
        sitemap.last_error = found[0].error
        await session.commit()
        logger.warning(
            "Couldn't read %s, so it wasn't checked: %s", sitemap.url, sitemap.last_error
        )
        return None

    await save_discovery(session, found, sitemap.schedule, sitemap.retention)
    # The tree is read after discovery, so sitemaps new in this check are included
    tree_ids = [tree_sitemap.id for tree_sitemap in await sitemap_tree(session, [sitemap.id])]
    in_tree = Page.id.in_(select(PageSitemap.page_id).where(PageSitemap.sitemap_id.in_(tree_ids)))
    run = IngestionRun(kind=RunKind.REFRESH, status=RunStatus.RUNNING, sitemap_ids=[sitemap.id])
    session.add(run)
    await session.flush()  # gives the run its id

    retained = await _apply_retention(session, in_tree, sitemap.retention, run)
    added, again = await _queue_pages(session, in_tree, run)
    # The summary of what the check found; changed_pages is counted later, as pages are fetched
    run.new_pages = added
    run.missing_pages = retained.missing
    run.archived_pages = retained.archived
    run.restored_pages = retained.restored
    run.total_pages = added + again + retained.restored
    if run.total_pages == 0:
        run.status, run.finished_at = RunStatus.DONE, datetime.now(UTC)
    sitemap.next_check_at = started + sitemap.schedule.interval
    await session.commit()
    logger.info(
        "Checked %s: %d new, %d missing (%d archived now), %d restored, %d to fetch again "
        "(ingestion %d)",
        sitemap.url,
        added,
        retained.missing,
        retained.archived,
        retained.restored,
        again,
        run.id,
    )
    return run


async def pages_still_queued(session: AsyncSession, sitemap_id: int) -> int:
    """How many pages of this sitemap, and of the sitemaps inside it, wait to be fetched."""
    tree_ids = [sitemap.id for sitemap in await sitemap_tree(session, [sitemap_id])]
    queued = await session.scalar(
        select(func.count(distinct(Page.id)))
        .join(PageSitemap, PageSitemap.page_id == Page.id)
        .where(PageSitemap.sitemap_id.in_(tree_ids), Page.fetch_requested_at.is_not(None))
    )
    return queued or 0


async def _apply_retention(
    session: AsyncSession, in_tree: ColumnElement[bool], retention: Retention, run: IngestionRun
) -> Retained:
    """Count one more missed check for each page of the sitemap that no sitemap lists any more,
    and archive the ones that reach the retention rule. Archived pages listed again are queued
    in the run, and their fetch makes them active."""
    # Listed by any sitemap, so a duplicate URL that one sitemap dropped isn't missing
    listed = Page.id.in_(select(PageSitemap.page_id).where(PageSitemap.removed_at.is_(None)))
    not_archived = Page.status != PageStatus.ARCHIVED

    restored = await session.execute(
        update(Page)
        .where(in_tree, listed, Page.status == PageStatus.ARCHIVED)
        .values(missing_count=0, fetch_requested_at=func.now(), run_id=run.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(  # missed checks count only in a row
        update(Page)
        .where(in_tree, listed, Page.missing_count > 0)
        .values(missing_count=0)
        .execution_options(synchronize_session=False)
    )
    missing = await session.execute(
        update(Page)
        .where(in_tree, ~listed, not_archived)
        .values(missing_count=Page.missing_count + 1)
        .execution_options(synchronize_session=False)
    )
    archived = await session.execute(
        update(Page)
        .where(in_tree, ~listed, not_archived, Page.missing_count >= retention.missing_checks)
        .values(status=PageStatus.ARCHIVED, archived_at=func.now())
        .execution_options(synchronize_session=False)
    )
    return Retained(_count(missing), _count(archived), _count(restored))


async def _queue_pages(
    session: AsyncSession, in_tree: ColumnElement[bool], run: IngestionRun
) -> tuple[int, int]:
    """Queue the sitemap's new pages, and every known page that isn't archived, in the run.
    Returns how many of each."""
    new_pages = await session.execute(
        update(Page)
        .where(in_tree, Page.status == PageStatus.DISCOVERED)
        .values(status=PageStatus.PENDING, fetch_requested_at=func.now(), run_id=run.id)
        .execution_options(synchronize_session=False)
    )
    # Known pages keep their status while they wait, so they stay searchable. A page that is
    # already queued (a redirect's destination, say) keeps its place and its run.
    known_pages = await session.execute(
        update(Page)
        .where(in_tree, Page.status != PageStatus.ARCHIVED, Page.fetch_requested_at.is_(None))
        .values(fetch_requested_at=func.now(), run_id=run.id)
        .execution_options(synchronize_session=False)
    )
    return _count(new_pages), _count(known_pages)


def _count(result: Result[Any]) -> int:
    """How many rows an UPDATE changed."""
    return cast(CursorResult[Any], result).rowcount
