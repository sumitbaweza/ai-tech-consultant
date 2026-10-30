"""The dashboard numbers and the progress stream (needs Postgres)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_session_factory
from app.api.main import app
from app.db import get_session
from app.models import (
    IngestionRun,
    Page,
    PageKind,
    PageSitemap,
    PageStatus,
    Retention,
    RunKind,
    RunStatus,
    Schedule,
    Sitemap,
)
from app.progress import knowledge_base_stats, run_progress

pytestmark = pytest.mark.db

SITE = "https://help.example"


async def test_stats_count_pages_duplicates_and_the_last_sync(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    sitemap = Sitemap(
        url=f"{SITE}/sitemap.xml",
        schedule=Schedule.DAILY,
        retention=Retention.AFTER_2,
        entry_count=5,  # one of its 5 entries repeats a URL
        last_checked_at=now - timedelta(hours=1),
    )
    db_session.add(sitemap)
    await db_session.flush()
    pages = [
        Page(
            url=f"{SITE}/embedded",
            kind=PageKind.ARTICLE,
            status=PageStatus.ACTIVE,
            content_hash="h1",
            embedded_hash="h1",
            fetched_at=now,
        ),
        Page(
            url=f"{SITE}/not-embedded-yet",
            kind=PageKind.ARTICLE,
            status=PageStatus.ACTIVE,
            content_hash="h2",
            fetched_at=now - timedelta(minutes=5),
        ),
        Page(url=f"{SITE}/failed", kind=PageKind.ARTICLE, status=PageStatus.FAILED),
        Page(url=f"{SITE}/pending", kind=PageKind.ARTICLE, status=PageStatus.PENDING),
    ]
    db_session.add_all(pages)
    await db_session.flush()
    db_session.add_all(
        PageSitemap(page_id=page.id, sitemap_id=sitemap.id, position=position)
        for position, page in enumerate(pages)
    )
    await db_session.commit()

    stats = await knowledge_base_stats(db_session)

    assert (stats.total_pages, stats.processed, stats.embedded) == (4, 3, 1)
    assert (stats.failed, stats.pending) == (1, 1)
    assert stats.duplicates_skipped == 1  # 5 entries, 4 distinct pages
    assert stats.last_sync == now


async def test_progress_measures_speed_and_time_left(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    run = IngestionRun(
        kind=RunKind.INITIAL,
        status=RunStatus.RUNNING,
        sitemap_ids=[],
        total_pages=3,
        started_at=now - timedelta(minutes=10),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add_all(
        [
            Page(
                url=f"{SITE}/1",
                kind=PageKind.ARTICLE,
                status=PageStatus.ACTIVE,
                run_id=run.id,
                fetched_at=now - timedelta(minutes=1),
            ),
            Page(
                url=f"{SITE}/2",
                kind=PageKind.ARTICLE,
                status=PageStatus.ACTIVE,
                run_id=run.id,
                fetched_at=now - timedelta(minutes=2),
            ),
            Page(
                url=f"{SITE}/3",
                kind=PageKind.ARTICLE,
                status=PageStatus.PENDING,
                run_id=run.id,
                fetch_requested_at=now,
            ),
        ]
    )
    await db_session.commit()

    progress = await run_progress(db_session, run.id)

    assert (progress.total, progress.done, progress.remaining) == (3, 2, 1)
    assert progress.by_status == {"active": 2, "pending": 1}
    assert progress.pages_per_minute == 0.4  # 2 pages in the last 5 minutes
    assert progress.estimate is not None and progress.estimate.minutes_to_finish == 2.5


async def test_progress_stream_and_stats_endpoints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        run = IngestionRun(
            kind=RunKind.INITIAL, status=RunStatus.DONE, sitemap_ids=[], total_pages=0
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_session] = test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            stream = await api.get(f"/v1/ingestions/{run_id}/progress")
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            # A finished run sends one event, then the stream ends
            [event] = [line for line in stream.text.splitlines() if line.startswith("data: ")]
            assert json.loads(event.removeprefix("data: "))["status"] == "done"

            assert (await api.get(f"/v1/ingestions/{run_id}")).json()["status"] == "done"
            assert (await api.get("/v1/ingestions/9999/progress")).status_code == 404
            assert (await api.get("/v1/stats")).json()["total_pages"] == 0
    finally:
        app.dependency_overrides.clear()
