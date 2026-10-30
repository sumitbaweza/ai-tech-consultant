"""Search the knowledge base: no LLM call, so it's fast and cheap."""

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import SessionDep, VoyageDep
from app.rag.embed import EmbeddingError
from app.rag.search import SearchHit, search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(8, ge=1, le=40)


class SearchResponse(BaseModel):
    query: str
    # "hybrid" ranks by meaning and exact words; "full_text" by exact words only, when the
    # embedding model is unavailable
    mode: Literal["hybrid", "full_text"]
    hits: list[SearchHit]


@router.post("/search")
async def search_chunks(
    body: SearchRequest, session: SessionDep, voyage: VoyageDep
) -> SearchResponse:
    """The chunks of active articles and screenshots that best match the query, each with its
    article's title and address. If the embedding model is unavailable, search still works on
    exact words, and `mode` says so."""
    query_vector: list[float] | None = None
    if voyage is not None:
        try:
            [query_vector] = await voyage.embed([body.query], "query")
        except EmbeddingError:
            logger.warning("Embedding the query failed; searching by exact words only")
    hits = await search(session, body.query, query_vector, body.limit)
    mode = "hybrid" if query_vector is not None else "full_text"
    return SearchResponse(query=body.query, mode=mode, hits=hits)
