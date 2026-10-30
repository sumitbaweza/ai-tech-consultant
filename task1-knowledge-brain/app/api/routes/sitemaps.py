"""Add sitemaps and preview what they contain, before anything is crawled."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from app.api.deps import CrawlerClientDep, SessionDep, SettingsDep
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.crawler.sitemap import discover
from app.ingest import Preview, build_preview, save_discovery
from app.models import Retention, Schedule

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
