"""Keep the search index current.

One round of index_pending():
- Rebuilds the text chunks of each active article whose content changed since it was last
  embedded (content_hash differs from embedded_hash), then marks it embedded.
- Adds a chunk for each described screenshot that doesn't have one yet. A screenshot whose row
  is replaced, because its article changed, loses its chunk with it (ON DELETE CASCADE) and
  gets a new one here.

Archived pages keep their chunks, so restoring a page needs no re-embedding: search simply
skips pages that aren't active.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, ChunkKind, Image, Page, PageKind, PageStatus
from app.rag.chunk import ChunkText, breadcrumb, chunk_article, image_chunk
from app.rag.embed import Embedder

PAGES_PER_ROUND = 25  # about 70 chunks: one or two embedding requests
IMAGES_PER_ROUND = 128  # one embedding request


async def index_pending(session: AsyncSession, embedder: Embedder) -> bool:
    """One round. Returns False when everything was already current."""
    pages_done = await _index_changed_pages(session, embedder)
    images_done = await _index_new_screenshots(session, embedder)
    return pages_done or images_done


async def _index_changed_pages(session: AsyncSession, embedder: Embedder) -> bool:
    pages = (
        await session.scalars(
            select(Page)
            .where(
                Page.status == PageStatus.ACTIVE,
                Page.kind.in_([PageKind.ARTICLE, PageKind.EXPERT]),
                Page.content_hash.is_not(None),
                Page.embedded_hash.is_distinct_from(Page.content_hash),
            )
            .order_by(Page.id)
            .limit(PAGES_PER_ROUND)
        )
    ).all()
    if not pages:
        return False

    planned = [(page, _article_chunks(page)) for page in pages]
    texts = [chunk.content for _, chunks in planned for chunk in chunks]
    # One embedding call for the whole round; the vectors come back in the same order
    vectors = iter(await embedder.embed(texts, "document") if texts else [])
    for page, chunks in planned:
        await session.execute(
            delete(Chunk).where(Chunk.page_id == page.id, Chunk.kind == ChunkKind.TEXT)
        )
        for position, chunk in enumerate(chunks, start=1):
            session.add(
                Chunk(
                    page_id=page.id,
                    kind=ChunkKind.TEXT,
                    position=position,
                    content=chunk.content,
                    token_count=chunk.token_count,
                    embedding=next(vectors),
                    embedding_model=embedder.model,
                )
            )
        page.embedded_hash = page.content_hash
    await session.commit()
    return True


async def _index_new_screenshots(session: AsyncSession, embedder: Embedder) -> bool:
    has_chunk = select(Chunk.id).where(Chunk.image_id == Image.id).exists()
    rows = (
        await session.execute(
            select(Image, Page)
            .join(Page, Page.id == Image.page_id)
            .where(Page.status == PageStatus.ACTIVE, Image.description.is_not(None), ~has_chunk)
            .order_by(Image.id)
            .limit(IMAGES_PER_ROUND)
        )
    ).all()
    if not rows:
        return False

    chunks = [
        image_chunk(
            breadcrumb(page.category, page.folder, page.title),
            image.position,
            image.alt,
            image.description or "",
        )
        for image, page in rows
    ]
    vectors = await embedder.embed([chunk.content for chunk in chunks], "document")
    for (image, page), chunk, vector in zip(rows, chunks, vectors, strict=True):
        session.add(
            Chunk(
                page_id=page.id,
                image_id=image.id,
                kind=ChunkKind.IMAGE,
                position=image.position,
                content=chunk.content,
                token_count=chunk.token_count,
                embedding=vector,
                embedding_model=embedder.model,
            )
        )
    await session.commit()
    return True


def _article_chunks(page: Page) -> list[ChunkText]:
    place = breadcrumb(page.category, page.folder, page.title)
    return chunk_article(page.content_md or "", place)
