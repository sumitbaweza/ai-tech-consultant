"""The Brain's HTTP API.

Open: GET /health, and the docs at /docs and /openapi.json.
Protected: everything under /v1, which needs a key in the X-API-Key header.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Knowledge Brain API",
    version="0.1.0",
    summary="Answers questions about the Brilliant Directories help center, with citations.",
    description="Every /v1 endpoint needs an `X-API-Key` header. Click **Authorize** to add it.",
)


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/health", tags=["health"])
async def health() -> Health:
    """Liveness check for Docker: answers as soon as the process is up."""
    return Health(status="ok")
