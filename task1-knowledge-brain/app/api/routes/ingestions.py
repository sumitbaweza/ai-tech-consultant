"""Confirm an ingestion: the step that actually starts the crawl."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import SessionDep
from app.ingest import confirm_ingestion
from app.models import RunKind, RunStatus

router = APIRouter(prefix="/ingestions", tags=["ingestions"])


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
