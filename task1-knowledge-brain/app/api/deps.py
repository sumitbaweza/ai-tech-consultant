"""Dependencies shared by the /v1 routes."""

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

import httpx2
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.crawler.http import make_client
from app.db import SessionFactory, get_session

# Declared as a security scheme, so /docs shows an Authorize button for the key
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """For a stream that outlives one request: it opens a fresh session for each read."""
    return SessionFactory


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def require_api_key(
    key: Annotated[str | None, Security(api_key_header)],
    settings: SettingsDep,
) -> None:
    """Reject the request with 401 unless X-API-Key matches one of BRAIN_API_KEYS."""
    # compare_digest takes the same time wherever the strings differ, so response times
    # reveal nothing about a key. Comparing bytes also copes with non-ASCII input.
    if key is None or not any(
        secrets.compare_digest(key.encode(), valid.encode()) for valid in settings.api_keys
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header",
        )


async def get_crawler_client(settings: SettingsDep) -> AsyncIterator[httpx2.AsyncClient]:
    """An HTTP client for reading the help center, closed when the request ends. Tests
    replace it with one that talks to a fake site."""
    async with make_client(settings) as client:
        yield client


CrawlerClientDep = Annotated[httpx2.AsyncClient, Depends(get_crawler_client)]
