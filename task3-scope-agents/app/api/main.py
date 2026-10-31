"""Task 3's HTTP API: the scope team, run in the background, with a human gate.

Open: GET /health, and the docs at /docs and /openapi.json.
Protected: everything under /v1, which needs a key in the X-API-Key header.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import store
from app.api.deps import require_api_key
from app.api.routes import playbook, projects, runs, versions
from app.brain_client import Brain, make_brain_client
from app.config import get_settings
from app.db import SessionFactory
from app.llm import get_claude
from app.orchestrator import Orchestrator, Team

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Build the agents' team once, and clean up runs a restart cut short."""
    settings = get_settings()
    async with SessionFactory() as session:
        interrupted = await store.fail_interrupted_runs(session)
    if interrupted:
        logger.warning("Marked %s interrupted runs as failed", interrupted)
    async with make_brain_client(settings) as http:
        team = Team(get_claude(), Brain(http), settings, SessionFactory)
        app.state.orchestrator = Orchestrator(team)
        yield


app = FastAPI(
    title="Meeting to Scope API",
    version="0.1.0",
    summary="A team of agents turns meeting transcripts into a scope, an estimate and a "
    "proposal that a human approves.",
    description="Every /v1 endpoint needs an `X-API-Key` header. Click **Authorize** to add it.",
    lifespan=lifespan,
)


@app.exception_handler(store.NotFound)
async def not_found(request: Request, error: store.NotFound) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=status.HTTP_404_NOT_FOUND)


@app.exception_handler(store.Conflict)
async def conflict(request: Request, error: store.Conflict) -> JSONResponse:
    return JSONResponse({"detail": str(error)}, status_code=status.HTTP_409_CONFLICT)


v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
v1.include_router(projects.router)
v1.include_router(runs.router)
v1.include_router(versions.router)
v1.include_router(playbook.router)
app.include_router(v1)


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/health", tags=["health"])
async def health() -> Health:
    """Liveness check for Docker: answers as soon as the process is up."""
    return Health(status="ok")
