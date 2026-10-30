"""Add sitemaps and preview what they contain, before anything is crawled. Then check them
again on demand, without waiting for their schedule."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.api.deps import CrawlerClientDep, SessionDep, SettingsDep
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import discover
from app.freshness import pages_still_queued
from app.ingest import Preview, build_preview, save_discovery
from app.models import Retention, Schedule, Sitemap

router = APIRouter(prefix="/sitemaps", tags=["sitemaps"])


class AddSitemaps(BaseModel):
    urls: list[HttpUrl] = Field(
        min_length=1,
        max_length=20,
        description="Sitemap URLs. Sitemap indexes are followed to the sitemaps inside them.",
    )
    schedule: Schedule = Field(Schedule.DAILY, description="How often to check them again")
    retention: Retention = Field(
        Retention.AFTER_2,
        description="How many checks in a row may miss a URL before its page is archived",
    )


class SitemapState(BaseModel):
    """A sitemap's schedule, and how its last check went."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    schedule: Schedule
    retention: Retention
    last_checked_at: datetime | None
    next_check_at: datetime | None  # None until its first crawl is confirmed
    last_error: str | None


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_sitemaps(
    body: AddSitemaps, session: SessionDep, settings: SettingsDep, client: CrawlerClientDep
) -> Preview:
    """Read the sitemaps (and any sitemaps they list), store the URLs they contain, and return
    the preview. No page is fetched until you confirm with POST /v1/ingestions."""
    limiter = HostRateLimiter(settings.crawler_requests_per_second)
    robots = RobotsRules(client, settings.crawler_user_agent, limiter)
    found = await discover([str(url) for url in body.urls], client, robots, limiter)
    root_ids = await save_discovery(session, found, body.schedule, body.retention)
    return await build_preview(session, root_ids, settings.crawler_requests_per_second)


@router.get("/{sitemap_id}/preview")
async def preview_sitemap(sitemap_id: int, session: SessionDep, settings: SettingsDep) -> Preview:
    """The preview again for one sitemap you added: counts, first and last URL per sitemap,
    duplicates, and how long the crawl will take."""
    try:
        return await build_preview(session, [sitemap_id], settings.crawler_requests_per_second)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{sitemap_id}/check", status_code=status.HTTP_202_ACCEPTED)
async def check_now(sitemap_id: int, session: SessionDep) -> SitemapState:
    """Check a sitemap now instead of at its next scheduled time. The worker starts within a
    minute: new URLs are added, and every page is fetched again as a `refresh` ingestion. The
    schedule then carries on from this check."""
    sitemap = await session.get(Sitemap, sitemap_id)
    if sitemap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No sitemap with id {sitemap_id}")
    if sitemap.parent_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Sitemap {sitemap_id} is checked with the sitemap that lists it, {sitemap.parent_id}",
        )
    if sitemap.next_check_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Confirm its first crawl with POST /v1/ingestions first"
        )
    if queued := await pages_still_queued(session, sitemap_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Its last crawl is still running; pages queued: {queued}"
        )
    sitemap.next_check_at = datetime.now(UTC)
    await session.commit()
    return SitemapState.model_validate(sitemap)
