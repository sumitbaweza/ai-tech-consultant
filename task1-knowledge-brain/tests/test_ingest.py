"""Saving what discovery found, the preview, and confirming an ingestion.

Everything except the estimate needs Postgres, so those tests are marked `db`.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_crawler_client
from app.api.main import app
from app.crawler.sitemap import FoundSitemap, PageEntry
from app.db import get_session
from app.ingest import DuplicateUrl, build_preview, confirm_ingestion, estimate, save_discovery
from app.models import Page, PageStatus, Retention, RunStatus, Schedule, Sitemap

SITE = "https://help.example"
INDEX, A, B = f"{SITE}/sitemap.xml", f"{SITE}/a.xml", f"{SITE}/b.xml"
P1, P2, P3 = (f"{SITE}/support/solutions/articles/{n}" for n in (1, 2, 3))


def found_sitemaps() -> list[FoundSitemap]:
    """An index with two sitemaps: P1 is listed twice inside A, and P2 is in both A and B."""
    return [
        FoundSitemap(INDEX, None, is_index=True, children=[A, B]),
        FoundSitemap(
            A, INDEX, pages=[PageEntry(P1, None), PageEntry(P2, None), PageEntry(P1, None)]
        ),
        FoundSitemap(B, INDEX, pages=[PageEntry(P2, None), PageEntry(P3, None)]),
    ]


def test_estimate_at_the_polite_rate() -> None:
    now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    result = estimate(1700, pages_per_minute=60, now=now)

    assert (result.pages_per_hour, result.pages_per_day) == (3600, 86400)
    assert result.minutes_to_finish == 28.3
    assert result.expected_completion == now + timedelta(minutes=1700 / 60)


@pytest.mark.db
async def test_preview_counts_duplicates_and_shows_first_and_last_urls(
    db_session: AsyncSession,
) -> None:
    root_ids = await save_discovery(
        db_session, found_sitemaps(), Schedule.WEEKLY, Retention.AFTER_15
    )

    preview = await build_preview(db_session, root_ids, requests_per_second=1.0)

    assert preview.total_sitemaps == 3
    assert preview.total_urls == 5  # 3 entries in A + 2 in B
    assert preview.unique_urls == 3
    assert preview.duplicate_urls == 2
    assert preview.duplicates == [DuplicateUrl(url=P2, sitemaps=[A, B])]
    assert preview.new_urls == 3
    by_url = {sitemap.url: sitemap for sitemap in preview.sitemaps}
    assert (by_url[A].first_url, by_url[A].last_url) == (P1, P2)
    assert (by_url[B].first_url, by_url[B].last_url) == (P2, P3)
    assert (by_url[INDEX].entries, by_url[INDEX].first_url) == (2, None)


@pytest.mark.db
async def test_confirm_queues_each_new_page_once(db_session: AsyncSession) -> None:
    root_ids = await save_discovery(db_session, found_sitemaps(), Schedule.DAILY, Retention.AFTER_2)

    run = await confirm_ingestion(db_session, root_ids)

    assert (run.total_pages, run.status) == (3, RunStatus.RUNNING)
    statuses = (await db_session.scalars(select(Page.status))).all()
    assert statuses == [PageStatus.PENDING] * 3
    root = await db_session.get(Sitemap, root_ids[0])
    assert root is not None and root.next_check_at is not None

    again = await confirm_ingestion(db_session, root_ids)  # nothing new to fetch
    assert (again.total_pages, again.status) == (0, RunStatus.DONE)


@pytest.mark.db
async def test_adding_a_sitemap_again_keeps_pages_already_crawled(db_session: AsyncSession) -> None:
    root_ids = await save_discovery(db_session, found_sitemaps(), Schedule.DAILY, Retention.AFTER_2)
    await confirm_ingestion(db_session, root_ids)
    await db_session.execute(update(Page).where(Page.url == P1).values(status=PageStatus.ACTIVE))
    await db_session.commit()

    root_ids = await save_discovery(db_session, found_sitemaps(), Schedule.DAILY, Retention.AFTER_2)
    preview = await build_preview(db_session, root_ids, requests_per_second=1.0)

    page = (await db_session.scalars(select(Page).where(Page.url == P1))).one()
    assert page.status == PageStatus.ACTIVE
    assert preview.new_urls == 0


@pytest.mark.db
async def test_api_add_preview_and_confirm(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    site = {
        INDEX: b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + f"<url><loc>{P1}</loc></url><url><loc>{P2}</loc></url>".encode()
        + b"</urlset>"
    }

    def fake_site(request: httpx2.Request) -> httpx2.Response:
        body = site.get(str(request.url))
        return httpx2.Response(200, content=body) if body else httpx2.Response(404)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def test_crawler_client() -> AsyncIterator[httpx2.AsyncClient]:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(fake_site)) as client:
            yield client

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_crawler_client] = test_crawler_client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            added = await api.post("/v1/sitemaps", json={"urls": [INDEX]})
            assert added.status_code == 201
            preview = added.json()
            assert (preview["total_urls"], preview["new_urls"]) == (2, 2)
            assert preview["sitemaps"][0]["first_url"] == P1

            [sitemap_id] = preview["sitemap_ids"]
            again = await api.get(f"/v1/sitemaps/{sitemap_id}/preview")
            assert again.json()["unique_urls"] == 2

            confirmed = await api.post("/v1/ingestions", json={"sitemap_ids": [sitemap_id]})
            assert confirmed.status_code == 201
            assert confirmed.json()["total_pages"] == 2

            assert (await api.get("/v1/sitemaps/9999/preview")).status_code == 404
            assert (
                await api.post("/v1/ingestions", json={"sitemap_ids": [9999]})
            ).status_code == 404
    finally:
        app.dependency_overrides.clear()
