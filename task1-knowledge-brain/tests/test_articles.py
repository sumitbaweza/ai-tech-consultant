"""Listing pages by status (the archived knowledge base) and an article's change history."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.main import app
from app.db import get_session
from app.models import Page, PageKind, PageStatus, PageVersion

pytestmark = pytest.mark.db  # every test here needs Postgres

SITE = "https://help.example/support/solutions/articles"


@pytest.fixture
async def api(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def test_pages_are_listed_by_status_the_latest_archived_first(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add_all(
            [
                Page(url=f"{SITE}/1", kind=PageKind.ARTICLE, status=PageStatus.ACTIVE),
                Page(
                    url=f"{SITE}/2",
                    kind=PageKind.ARTICLE,
                    status=PageStatus.ARCHIVED,
                    archived_at=now - timedelta(days=2),
                    missing_count=2,
                ),
                Page(
                    url=f"{SITE}/3",
                    kind=PageKind.ARTICLE,
                    status=PageStatus.ARCHIVED,
                    archived_at=now,
                    missing_count=2,
                ),
                Page(
                    url=f"{SITE}/4",
                    kind=PageKind.ARTICLE,
                    status=PageStatus.FAILED,
                    error="The server answered HTTP 500",
                ),
            ]
        )
        await session.commit()

    archived = (await api.get("/v1/articles", params={"status": "archived"})).json()
    failed = (await api.get("/v1/articles", params={"status": "failed"})).json()
    first_two = (await api.get("/v1/articles", params={"limit": 2})).json()

    assert archived["total"] == 2
    assert [item["url"] for item in archived["items"]] == [f"{SITE}/3", f"{SITE}/2"]
    assert archived["items"][0]["missing_count"] == 2
    assert [item["error"] for item in failed["items"]] == ["The server answered HTTP 500"]
    assert (first_two["total"], len(first_two["items"])) == (4, 2)
    assert (await api.get("/v1/articles", params={"status": "gone"})).status_code == 422


async def test_the_change_history_shows_what_changed_newest_first(
    session_factory: async_sessionmaker[AsyncSession], api: httpx.AsyncClient
) -> None:
    async with session_factory() as session:
        page = Page(
            url=f"{SITE}/labels", kind=PageKind.ARTICLE, status=PageStatus.ACTIVE, title="Labels"
        )
        session.add(page)
        await session.flush()
        for number, text in enumerate(
            ["Open Settings.\nClick Save.", "Open Settings.\nClick Apply."]
        ):
            session.add(
                PageVersion(
                    page_id=page.id, content_hash=str(number) * 64, title="Labels", content_md=text
                )
            )
            await session.flush()  # one at a time, so the ids follow the history
        await session.commit()

    history = (await api.get(f"/v1/articles/{page.id}/history")).json()
    newest_only = (await api.get(f"/v1/articles/{page.id}/history", params={"limit": 1})).json()

    newest, first = history["versions"]
    assert first["diff"] is None
    assert "-Click Save.\n+Click Apply." in newest["diff"]
    # Showing only the newest version still diffs it against the one before
    assert [version["diff"] for version in newest_only["versions"]] == [newest["diff"]]
    assert (await api.get("/v1/articles/999999/history")).status_code == 404
