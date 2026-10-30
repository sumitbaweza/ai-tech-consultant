"""The screens' client for the Brain's API.

The screens are one more client of the API, like the MCP server. Each response is read with the
API's own Pydantic model, so a change to the API that breaks a screen fails here, loudly.
"""

from typing import Any

import httpx2
import streamlit as st

from app.api.routes.articles import ArticleHistory, ArticleList
from app.api.routes.expert_tasks import ExpertTaskList, ExpertTaskView
from app.api.routes.ingestions import Ingestion
from app.api.routes.sitemaps import SitemapState
from app.api_client import api_key, error_reason
from app.config import get_settings
from app.ingest import Preview
from app.models import ExpertStatus, PageStatus, Retention, Schedule
from app.progress import Stats
from app.rag.answer import AskResult, ChatTurn


class BrainError(Exception):
    """The API refused, or couldn't be reached. The message is meant for the screen."""


class Brain:
    def __init__(self, client: httpx2.Client) -> None:
        self._client = client

    # --- Dashboard ------------------------------------------------------------------------

    def stats(self) -> Stats:
        """The dashboard's numbers, and the latest run."""
        return Stats.model_validate(self._request("GET", "/v1/stats"))

    # --- Sources --------------------------------------------------------------------------

    def add_sitemaps(self, urls: list[str], schedule: Schedule, retention: Retention) -> Preview:
        """Read the sitemaps and the ones inside them; nothing is crawled until confirm()."""
        body = {"urls": urls, "schedule": schedule, "retention": retention}
        return Preview.model_validate(self._request("POST", "/v1/sitemaps", body))

    def preview(self, sitemap_id: int) -> Preview:
        return Preview.model_validate(self._request("GET", f"/v1/sitemaps/{sitemap_id}/preview"))

    def confirm(self, sitemap_ids: list[int]) -> Ingestion:
        body = {"sitemap_ids": sitemap_ids}
        return Ingestion.model_validate(self._request("POST", "/v1/ingestions", body))

    def sitemaps(self) -> list[SitemapState]:
        return [SitemapState.model_validate(item) for item in self._request("GET", "/v1/sitemaps")]

    def check_now(self, sitemap_id: int) -> SitemapState:
        reply = self._request("POST", f"/v1/sitemaps/{sitemap_id}/check")
        return SitemapState.model_validate(reply)

    # --- Change history and archive -------------------------------------------------------

    def articles(self, status: PageStatus, limit: int = 100) -> ArticleList:
        params = {"status": status, "limit": limit}
        return ArticleList.model_validate(self._request("GET", "/v1/articles", params=params))

    def history(self, article_id: int) -> ArticleHistory:
        reply = self._request("GET", f"/v1/articles/{article_id}/history")
        return ArticleHistory.model_validate(reply)

    # --- Chat -----------------------------------------------------------------------------

    def ask(self, question: str, history: list[ChatTurn]) -> AskResult:
        body = {"question": question, "history": [turn.model_dump() for turn in history]}
        return AskResult.model_validate(self._request("POST", "/v1/ask", body))

    def flag(self, question: str, answer: str) -> ExpertTaskView:
        """A thumbs down: the answer was wrong, so the question goes to the expert queue."""
        body = {"question": question, "answer": answer}
        return ExpertTaskView.model_validate(self._request("POST", "/v1/expert-tasks", body))

    # --- Expert queue ---------------------------------------------------------------------

    def expert_tasks(self, status: ExpertStatus) -> ExpertTaskList:
        reply = self._request("GET", "/v1/expert-tasks", params={"status": status})
        return ExpertTaskList.model_validate(reply)

    def answer_task(self, task_id: int, answer: str, expert: str) -> ExpertTaskView:
        body = {"answer": answer, "expert": expert}
        reply = self._request("POST", f"/v1/expert-tasks/{task_id}/answer", body)
        return ExpertTaskView.model_validate(reply)

    def dismiss_task(self, task_id: int, expert: str) -> ExpertTaskView:
        reply = self._request("POST", f"/v1/expert-tasks/{task_id}/dismiss", {"expert": expert})
        return ExpertTaskView.model_validate(reply)

    def archive_answer(self, task_id: int) -> ExpertTaskView:
        reply = self._request("POST", f"/v1/expert-tasks/{task_id}/archive-answer")
        return ExpertTaskView.model_validate(reply)

    # --- Every call -----------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, path, json=body, params=params)
        except httpx2.HTTPError as error:
            raise BrainError(f"The Brain's API can't be reached: {error}") from error
        if response.status_code >= 400:
            reason = error_reason(response)
            raise BrainError(f"The Brain's API answered HTTP {response.status_code}: {reason}")
        return response.json()


@st.cache_resource
def get_brain() -> Brain:
    """One client for the whole app: Streamlit keeps it across reruns and sessions."""
    settings = get_settings()
    client = httpx2.Client(
        base_url=settings.brain_api_url,
        headers={"X-API-Key": api_key(settings)},
        timeout=httpx2.Timeout(120.0),  # an answer can take Sonnet a while
    )
    return Brain(client)
