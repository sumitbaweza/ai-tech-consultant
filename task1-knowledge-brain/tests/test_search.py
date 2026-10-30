"""Hybrid search and POST /v1/search (needs Postgres).

Each chunk gets a hand-made embedding: a 1,024-number vector pointing in one direction. A query
vector pointing the same way has cosine similarity 1; any other direction has 0.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.main import app
from app.db import get_session
from app.models import Chunk, ChunkKind, Image, Page, PageKind, PageStatus
from app.rag.embed import InputType, get_voyage
from app.rag.search import SearchHit, search

pytestmark = pytest.mark.db

SITE = "https://help.example/support/solutions/articles"


def unit(dimension: int) -> list[float]:
    vector = [0.0] * 1024
    vector[dimension] = 1.0
    return vector


async def page_with_chunk(
    session: AsyncSession,
    slug: str,
    content: str,
    direction: int,
    status: PageStatus = PageStatus.ACTIVE,
) -> Page:
    page = Page(url=f"{SITE}/{slug}", kind=PageKind.ARTICLE, status=status, title=slug)
    session.add(page)
    await session.flush()
    session.add(
        Chunk(
            page_id=page.id,
            kind=ChunkKind.TEXT,
            position=1,
            content=content,
            token_count=10,
            embedding=unit(direction),
            embedding_model="test",
        )
    )
    await session.commit()
    return page


async def help_center(session: AsyncSession) -> None:
    labels = await page_with_chunk(
        session, "1-text-labels", "Text Labels let you rename the words visitors see.", 0
    )
    await page_with_chunk(
        session, "2-email-forwarders", "Forward emails from your domain to another inbox.", 1
    )
    await page_with_chunk(
        session, "3-old-labels", "Text Labels, the archived copy.", 0, PageStatus.ARCHIVED
    )
    screenshot = Image(page_id=labels.id, position=1, url="https://cdn.example/date.png")
    session.add(screenshot)
    await session.flush()
    session.add(
        Chunk(
            page_id=labels.id,
            image_id=screenshot.id,
            kind=ChunkKind.IMAGE,
            position=1,
            content="Screenshot: the Date edited filter open on its calendar.",
            token_count=10,
            embedding=unit(2),
            embedding_model="test",
        )
    )
    await session.commit()


async def test_meaning_finds_the_chunk_even_without_shared_words(db_session: AsyncSession) -> None:
    await help_center(db_session)

    hits = await search(db_session, "how do I change wording", unit(0))

    assert hits[0].url == f"{SITE}/1-text-labels"
    assert hits[0].similarity == pytest.approx(1.0)


async def test_exact_words_work_without_any_vector(db_session: AsyncSession) -> None:
    await help_center(db_session)

    hits = await search(db_session, "forward emails", None)

    assert [hit.url for hit in hits] == [f"{SITE}/2-email-forwarders"]
    assert hits[0].similarity is None


async def test_both_lists_are_fused_and_a_screenshot_hit_carries_its_image(
    db_session: AsyncSession,
) -> None:
    await help_center(db_session)

    hits = await search(db_session, "Date edited", unit(0))

    best_of_each_kind: dict[ChunkKind, SearchHit] = {}
    for hit in hits:
        best_of_each_kind.setdefault(hit.kind, hit)
    image_hit, text_hit = best_of_each_kind[ChunkKind.IMAGE], best_of_each_kind[ChunkKind.TEXT]
    assert image_hit.image_url == "https://cdn.example/date.png"  # found by its words
    assert text_hit.url == f"{SITE}/1-text-labels"  # found by meaning


async def test_archived_pages_are_never_returned(db_session: AsyncSession) -> None:
    await help_center(db_session)

    hits = await search(db_session, "Text Labels archived copy", unit(0), limit=40)

    assert f"{SITE}/3-old-labels" not in {hit.url for hit in hits}


class FakeEmbedder:
    model = "fake-embedder"

    async def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        return [unit(0) for _ in texts]


async def test_the_search_endpoint_in_both_modes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await help_center(session)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            app.dependency_overrides[get_voyage] = FakeEmbedder
            hybrid = (await api.post("/v1/search", json={"query": "rename words"})).json()
            assert hybrid["mode"] == "hybrid"
            assert hybrid["hits"][0]["url"] == f"{SITE}/1-text-labels"

            app.dependency_overrides[get_voyage] = lambda: None  # no embedding model
            words_only = (await api.post("/v1/search", json={"query": "forward emails"})).json()
            assert words_only["mode"] == "full_text"
            assert [hit["url"] for hit in words_only["hits"]] == [f"{SITE}/2-email-forwarders"]

            assert (await api.post("/v1/search", json={"query": ""})).status_code == 422
    finally:
        app.dependency_overrides.clear()
