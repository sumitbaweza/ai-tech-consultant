"""Hybrid search: by meaning (pgvector) and by exact words (Postgres full-text), merged.

Vector search finds chunks that mean the same as the question in other words. Full-text search
finds exact setting names and labels, which embeddings can blur. Each takes its top CANDIDATES,
and Reciprocal Rank Fusion merges the two lists: a chunk scores 1 / (RRF_K + rank) in each list
it appears in, so a chunk ranked high in either list rises, and one in both rises most. RRF
needs no tuning, because it uses ranks, not the two methods' scores, which aren't comparable.

Only chunks of active pages are searched, so an archived article drops out of answers at once.
"""

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Float, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, ChunkKind, Image, Page, PageKind, PageStatus

CANDIDATES = 40
RRF_K = 60  # the constant from the paper that introduced RRF: it softens the gap between ranks


class SearchHit(BaseModel):
    chunk_id: int
    page_id: int
    page_kind: PageKind  # an article, or an expert's answer from the expert queue
    kind: ChunkKind
    title: str | None
    url: str
    image_url: str | None  # for a screenshot chunk: the screenshot itself
    content: str
    score: float  # the fused RRF score: higher is better
    similarity: float | None  # cosine similarity to the question, if vector search found it


async def search(
    session: AsyncSession, query: str, query_vector: list[float] | None, limit: int = 8
) -> list[SearchHit]:
    """The best `limit` chunks for the query. Without a query vector (no embedding model
    available) it ranks by exact words alone."""
    active = Page.status == PageStatus.ACTIVE

    words = func.websearch_to_tsquery("english", query)  # accepts any text, never a syntax error
    text_rank = func.ts_rank_cd(Chunk.tsv, words)
    text_hits = (
        select(
            Chunk.id.label("chunk_id"),
            func.row_number().over(order_by=text_rank.desc()).label("rank"),
        )
        .join(Page, Page.id == Chunk.page_id)
        .where(active, Chunk.tsv.bool_op("@@")(words))
        .order_by(text_rank.desc())
        .limit(CANDIDATES)
        .cte("text_hits")
    )
    text_list = select(
        text_hits.c.chunk_id, text_hits.c.rank, literal(None, Float()).label("similarity")
    )

    if query_vector is None:
        both = text_list.subquery("both_lists")
    else:
        # <=> is pgvector's cosine distance: 0 for the same direction, 2 for opposite ones
        distance: ColumnElement[float] = Chunk.embedding.op("<=>", return_type=Float())(
            query_vector
        )
        vector_hits = (
            select(
                Chunk.id.label("chunk_id"),
                func.row_number().over(order_by=distance).label("rank"),
                (literal(1.0) - distance).label("similarity"),
            )
            .join(Page, Page.id == Chunk.page_id)
            .where(active)
            .order_by(distance)
            .limit(CANDIDATES)
            .cte("vector_hits")
        )
        vector_list = select(vector_hits.c.chunk_id, vector_hits.c.rank, vector_hits.c.similarity)
        both = union_all(text_list, vector_list).subquery("both_lists")

    score = func.sum(literal(1.0) / (RRF_K + both.c.rank))
    fused = (
        select(
            both.c.chunk_id, score.label("score"), func.max(both.c.similarity).label("similarity")
        )
        .group_by(both.c.chunk_id)
        .order_by(score.desc())
        .limit(limit)
        .subquery("fused")
    )
    rows = await session.execute(
        select(
            fused.c.chunk_id,
            fused.c.score,
            fused.c.similarity,
            Chunk.page_id,
            Page.kind.label("page_kind"),
            Chunk.kind,
            Chunk.content,
            Page.title,
            Page.url,
            Image.url.label("image_url"),
        )
        .select_from(fused)
        .join(Chunk, Chunk.id == fused.c.chunk_id)
        .join(Page, Page.id == Chunk.page_id)
        .outerjoin(Image, Image.id == Chunk.image_id)
        .order_by(fused.c.score.desc(), fused.c.chunk_id)
    )
    return [
        SearchHit(
            chunk_id=row.chunk_id,
            page_id=row.page_id,
            page_kind=row.page_kind,
            kind=row.kind,
            title=row.title,
            url=row.url,
            image_url=row.image_url,
            content=row.content,
            score=float(row.score),
            similarity=float(row.similarity) if row.similarity is not None else None,
        )
        for row in rows
    ]
