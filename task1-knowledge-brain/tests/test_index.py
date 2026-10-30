"""Keeping the search index current (needs Postgres)."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, ChunkKind, Image, Page, PageKind, PageStatus
from app.rag.embed import InputType
from app.rag.index import index_pending

pytestmark = pytest.mark.db

ARTICLE = "## Overview\n\nLabels change the words visitors see.\n\n[Screenshot 1: The list]"
CDN = "https://cdn.example"


def unit(dimension: int) -> list[float]:
    vector = [0.0] * 1024
    vector[dimension] = 1.0
    return vector


class FakeEmbedder:
    model = "fake-embedder"

    def __init__(self) -> None:
        self.calls: list[tuple[int, InputType]] = []

    async def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        self.calls.append((len(texts), input_type))
        return [unit(number % 1024) for number in range(len(texts))]


async def an_article_with_screenshots(session: AsyncSession) -> Page:
    page = Page(
        url="https://help.example/support/solutions/articles/1-labels",
        kind=PageKind.ARTICLE,
        status=PageStatus.ACTIVE,
        title="Settings » Text Labels",
        category="Settings",
        folder="Text Labels",
        content_md=ARTICLE,
        content_hash="hash-1",
    )
    listing = Page(
        url="https://help.example/support/solutions/folders/1",
        kind=PageKind.LISTING,
        status=PageStatus.ACTIVE,
        content_md="",
        content_hash="hash-listing",
    )
    session.add_all([page, listing])
    await session.flush()
    described = Image(page_id=page.id, position=1, url=f"{CDN}/1.png", description="A list")
    not_described_yet = Image(page_id=page.id, position=2, url=f"{CDN}/2.png")
    session.add_all([described, not_described_yet])
    await session.commit()
    return page


async def chunk_kinds(session: AsyncSession) -> list[tuple[ChunkKind, int | None]]:
    rows = await session.execute(
        select(Chunk.kind, Chunk.image_id).order_by(Chunk.kind, Chunk.position)
    )
    return [(kind, image_id) for kind, image_id in rows]


async def test_articles_and_described_screenshots_get_chunks(db_session: AsyncSession) -> None:
    page = await an_article_with_screenshots(db_session)
    embedder = FakeEmbedder()

    assert await index_pending(db_session, embedder)

    described = await db_session.scalar(select(Image.id).where(Image.position == 1))
    # Sorted by kind as stored: "image" before "text"
    assert await chunk_kinds(db_session) == [(ChunkKind.IMAGE, described), (ChunkKind.TEXT, None)]
    assert page.embedded_hash == "hash-1"
    assert embedder.calls == [(1, "document"), (1, "document")]  # the text chunk, the screenshot
    text = await db_session.scalar(select(Chunk.content).where(Chunk.kind == ChunkKind.TEXT))
    assert text is not None and text.startswith("Settings > Text Labels > Settings » Text Labels")

    assert not await index_pending(db_session, embedder)  # nothing new: no more calls
    assert len(embedder.calls) == 2


async def test_changed_content_replaces_the_text_chunks_only(db_session: AsyncSession) -> None:
    page = await an_article_with_screenshots(db_session)
    await index_pending(db_session, FakeEmbedder())

    page.content_md = "## Overview\n\nLabels now also rename buttons."
    page.content_hash = "hash-2"
    await db_session.commit()
    await index_pending(db_session, FakeEmbedder())

    texts = (
        await db_session.scalars(select(Chunk.content).where(Chunk.kind == ChunkKind.TEXT))
    ).all()
    assert len(texts) == 1 and "rename buttons" in texts[0]
    images = await db_session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.kind == ChunkKind.IMAGE)
    )
    assert images == 1
    assert page.embedded_hash == "hash-2"
