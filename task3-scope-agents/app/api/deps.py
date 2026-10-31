"""Dependencies shared by the /v1 routes, and the background tasks that run the agents."""

import asyncio
import logging
import secrets
from collections.abc import Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# Declared as a security scheme, so /docs shows an Authorize button for the key
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_api_key(
    key: Annotated[str | None, Security(api_key_header)],
    settings: SettingsDep,
) -> None:
    """Reject the request with 401 unless X-API-Key matches one of SCOPE_API_KEYS."""
    # compare_digest takes the same time wherever the strings differ, so response times
    # reveal nothing about a key. Comparing bytes also copes with non-ASCII input.
    if key is None or not any(
        secrets.compare_digest(key.encode(), valid.encode()) for valid in settings.api_keys
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


def get_orchestrator(request: Request) -> Orchestrator:
    """The orchestrator built at startup (app/api/main.py). Tests replace it with one that
    uses a fake Claude and a fake Brain."""
    orchestrator: Orchestrator = request.app.state.orchestrator
    return orchestrator


def require_claude(settings: SettingsDep) -> None:
    """A clear 503 before a run starts, instead of a run that fails at its first agent."""
    if not settings.anthropic_api_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The agents need ANTHROPIC_API_KEY in .env",
        )


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]

# Background runs. asyncio keeps only a weak reference to a task, so each one is held here
# until it finishes, or it could be garbage-collected halfway through a run.
_running: set[asyncio.Task[None]] = set()


def launch(work: Coroutine[Any, Any, None]) -> None:
    """Start an agent run in the background; the request returns at once and the screen
    follows the run's steps."""
    task = asyncio.create_task(work)
    _running.add(task)
    task.add_done_callback(_running.discard)


async def wait_for_runs() -> None:
    """Wait for every background run to finish. Tests use it to check a run's result."""
    while _running:
        await asyncio.gather(*list(_running))
