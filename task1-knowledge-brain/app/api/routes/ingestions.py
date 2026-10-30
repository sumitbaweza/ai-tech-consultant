"""Confirm an ingestion (the step that starts the crawl), then follow its progress."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import SessionDep, SessionFactoryDep
from app.ingest import confirm_ingestion
from app.models import RunKind, RunStatus
from app.progress import Progress, run_progress

router = APIRouter(prefix="/ingestions", tags=["ingestions"])

PROGRESS_EVERY_SECONDS = 2.0


class ConfirmIngestion(BaseModel):
    sitemap_ids: list[int] = Field(
        min_length=1, description="The sitemap_ids from the preview of POST /v1/sitemaps"
    )


class Ingestion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: RunKind
    status: RunStatus
    sitemap_ids: list[int]
    total_pages: int
    started_at: datetime


@router.post("", status_code=status.HTTP_201_CREATED)
async def confirm(body: ConfirmIngestion, session: SessionDep) -> Ingestion:
    """Queue every new page of these sitemaps for the crawler, and start their re-check
    schedule. Pages already in the knowledge base aren't fetched again here."""
    try:
        run = await confirm_ingestion(session, body.sitemap_ids)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Ingestion.model_validate(run)


@router.get("/{run_id}")
async def ingestion_progress(run_id: int, session: SessionDep) -> Progress:
    """Where an ingestion stands now: counts, measured pages per minute, and time left."""
    try:
        return await run_progress(session, run_id)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/{run_id}/progress", response_class=StreamingResponse)
async def stream_progress(run_id: int, session_factory: SessionFactoryDep) -> StreamingResponse:
    """Server-Sent Events: every 2 seconds, a `data:` line with the same JSON as
    GET /v1/ingestions/{run_id}, until the ingestion finishes."""
    async with session_factory() as session:
        try:
            await run_progress(session, run_id)  # a 404 now, rather than a broken stream later
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    async def events() -> AsyncIterator[str]:
        while True:
            # A fresh session each time, so every read sees the worker's latest commits
            async with session_factory() as session:
                progress = await run_progress(session, run_id)
            yield f"data: {progress.model_dump_json()}\n\n"
            if progress.status != RunStatus.RUNNING:
                return
            await asyncio.sleep(PROGRESS_EVERY_SECONDS)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
