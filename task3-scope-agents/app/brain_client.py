"""Task 3's client for the Knowledge Brain (Task 1), over its HTTP API only.

Task 3 never imports Task 1's code or reads its tables (the `scope` role can't). The models
below copy only the fields of the Brain's OpenAPI schema that the fit checker uses, and ignore
the rest, so the Brain can add fields without breaking this client.

Every failure (Brain down, timeout, bad key, bad reply) becomes BrainUnavailable. The fit
checker turns that into an "unverified" result, so a run goes on without the Brain.
"""

import logging
from collections.abc import Mapping
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings

logger = logging.getLogger(__name__)


class BrainUnavailable(Exception):
    """The Brain couldn't answer: down, slow, refused the key, or replied with nonsense."""


class _BrainModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SearchHit(_BrainModel):
    """One chunk of a help-center article that matched the search."""

    chunk_id: int
    page_id: int
    kind: str  # "text" or "image": a chunk of article text, or a screenshot's description
    title: str | None
    url: str
    content: str
    score: float


class SearchResponse(_BrainModel):
    mode: Literal["hybrid", "full_text"]
    hits: list[SearchHit]


class Citation(_BrainModel):
    origin: Literal["article", "expert"]
    title: str | None
    url: str


class AskResult(_BrainModel):
    answered: bool
    answer: str
    citations: list[Citation]
    confidence: float


class Brain:
    """Calls the Brain's /v1/search and /v1/ask with this task's API key."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def search(self, query: str, limit: int = 6) -> list[SearchHit]:
        """The help-center chunks closest to `query`. No LLM runs, so it's fast and free."""
        body = {"query": query[:1000], "limit": limit}
        return (await self._post("/v1/search", body, SearchResponse)).hits

    async def ask(self, question: str) -> AskResult:
        """A cited answer from the help center. Questions it can't answer go to the Brain's
        expert queue, so the fit checker only asks about likely settings."""
        body = {"question": question[:2000]}
        return await self._post("/v1/ask", body, AskResult)

    async def _post[T: BaseModel](self, path: str, body: Mapping[str, object], reply: type[T]) -> T:
        try:
            response = await self._client.post(path, json=body)
            response.raise_for_status()
            return reply.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as error:
            # ValidationError: the body wasn't the JSON we expected. Logged here; the caller
            # only needs to know the Brain didn't answer.
            logger.warning("The Brain's %s failed: %s", path, error)
            raise BrainUnavailable(f"{path}: {error}") from error


def make_brain_client(settings: Settings) -> httpx.AsyncClient:
    """An HTTP client for the Brain. /v1/ask runs a Sonnet answer, so allow it a minute."""
    return httpx.AsyncClient(
        base_url=settings.brain_api_url,
        headers={"X-API-Key": settings.brain_api_key.get_secret_value()},
        timeout=httpx.Timeout(90.0, connect=5.0),
    )
