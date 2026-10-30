"""The dashboard numbers for the whole knowledge base."""

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.progress import Stats, knowledge_base_stats

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def stats(session: SessionDep) -> Stats:
    """Processed, embedded, failed and pending pages, duplicate URLs skipped, and the last sync."""
    return await knowledge_base_stats(session)
