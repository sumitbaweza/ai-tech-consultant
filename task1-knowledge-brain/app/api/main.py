"""The Brain's HTTP API.

Open: GET /health, and the docs at /docs and /openapi.json.
Protected: everything under /v1, which needs a key in the X-API-Key header.
"""

from typing import Literal

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.api.routes import articles, ask, ingestions, search, sitemaps, stats

app = FastAPI(
    title="Knowledge Brain API",
    version="0.1.0",
    summary="Answers questions about the Brilliant Directories help center, with citations.",
    description="Every /v1 endpoint needs an `X-API-Key` header. Click **Authorize** to add it.",
)

v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
v1.include_router(sitemaps.router)
v1.include_router(ingestions.router)
v1.include_router(stats.router)
v1.include_router(search.router)
v1.include_router(ask.router)
v1.include_router(articles.router)
app.include_router(v1)


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/health", tags=["health"])
async def health() -> Health:
    """Liveness check for Docker: answers as soon as the process is up."""
    return Health(status="ok")
