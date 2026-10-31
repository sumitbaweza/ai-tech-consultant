"""The seed snapshot: export a small help center, empty the database, load it back."""

import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Base,
    Chunk,
    ChunkKind,
    Page,
    PageKind,
    PageSitemap,
    PageStatus,
    PageVersion,
    Retention,
    Schedule,
    Sitemap,
)
from app.rag.search import search
from app.seed import export, load, pack_vector, unpack_vector
from tests.test_search import SITE, help_center, unit


def test_a_vector_survives_float16_within_rounding() -> None:
    vector = [0.1234, -0.5, 0.0, 0.9999, -0.0042]

    restored = unpack_vector(pack_vector(vector))

    assert len(restored) == len(vector)
    assert all(abs(a - b) < 1e-3 for a, b in zip(restored, vector, strict=True))


async def _a_crawled_help_center(session: AsyncSession) -> None:
    """test_search's help center, listed in a sitemap, plus one expert answer."""
    await help_center(session)
    sitemap = Sitemap(
        url=f"{SITE}/sitemap.xml",
        schedule=Schedule.DAILY,
        retention=Retention.AFTER_2,
        entry_count=3,
    )
    expert = Page(
        url="expert://1",
        kind=PageKind.EXPERT,
        status=PageStatus.ACTIVE,
        content_md="Ask the expert",
        content_hash="e" * 64,
    )
    session.add_all([sitemap, expert])
    await session.flush()
    for position, page in enumerate(
        await session.scalars(select(Page).where(Page.kind == "article"))
    ):
        page.content_md, page.content_hash = f"Content of {page.url}", f"{position}" * 64
        session.add(PageSitemap(page_id=page.id, sitemap_id=sitemap.id, position=position))
    session.add(
        Chunk(
            page_id=expert.id,
            kind=ChunkKind.TEXT,
            position=1,
            content="Ask the expert",
            token_count=3,
            embedding=unit(3),
            embedding_model="test",
        )
    )
    await session.commit()


async def _empty(session: AsyncSession) -> None:
    tables = ", ".join(table.fullname for table in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await session.commit()


async def _count(session: AsyncSession, model: type[Base]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.db
async def test_a_snapshot_loads_back_into_an_empty_knowledge_base(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _a_crawled_help_center(db_session)
    seed = tmp_path / "seed.jsonl.gz"

    exported = await export(db_session, seed)
    await _empty(db_session)
    loaded = await load(db_session, seed)

    # The expert answer and its chunk stay behind
    assert (
        exported
        == loaded
        == {
            "sitemaps": 1,
            "pages": 3,
            "page_sitemaps": 3,
            "images": 1,
            "chunks": 4,
        }
    )
    with gzip.open(seed, "rt", encoding="utf-8") as file:
        assert json.loads(file.readline())["counts"] == exported

    # Search works on the loaded rows, vectors included
    hits = await search(db_session, "how do I change wording", unit(0))
    assert hits[0].url == f"{SITE}/1-text-labels"
    assert hits[0].similarity == pytest.approx(1.0, abs=1e-3)

    # Each page has its first version, and the sitemap its next check
    assert await _count(db_session, PageVersion) == 3
    sitemap = await db_session.scalar(select(Sitemap))
    assert sitemap is not None and sitemap.next_check_at is not None

    # The id sequences moved past the loaded rows, so a new page gets a fresh id
    db_session.add(Page(url=f"{SITE}/4-new", kind=PageKind.ARTICLE, status=PageStatus.PENDING))
    await db_session.commit()
    assert await _count(db_session, Page) == 4


@pytest.mark.db
async def test_a_knowledge_base_with_pages_is_left_alone(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _a_crawled_help_center(db_session)
    seed = tmp_path / "seed.jsonl.gz"
    await export(db_session, seed)

    assert await load(db_session, seed) is None
    assert await _count(db_session, Page) == 4  # 3 articles and the expert answer, unchanged
