"""The screens' client for this task's API.

The screens are one more client of the API: they never touch the database. Each response is
read with the API's own Pydantic model, so a change to the API that breaks a screen fails here,
loudly.
"""

from typing import Any

import httpx
import streamlit as st

from app.agents.human_approval import TaskEdit
from app.api.routes.playbook import PlaybookView
from app.api.views import ProjectView, RunView, VersionView
from app.config import get_settings


class ScopeAPIError(Exception):
    """The API refused, or couldn't be reached. The message is meant for the screen."""


class ScopeAPI:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    # --- Projects and meetings ------------------------------------------------------------

    def projects(self) -> list[ProjectView]:
        return [ProjectView.model_validate(p) for p in self._request("GET", "/v1/projects")]

    def project(self, project_id: int) -> ProjectView:
        return ProjectView.model_validate(self._request("GET", f"/v1/projects/{project_id}"))

    def create_project(self, name: str, client: str) -> ProjectView:
        body = {"name": name, "client": client}
        return ProjectView.model_validate(self._request("POST", "/v1/projects", body))

    def add_meeting(self, project_id: int, title: str, transcript: str) -> RunView:
        body = {"title": title, "transcript": transcript}
        reply = self._request("POST", f"/v1/projects/{project_id}/meetings", body)
        return RunView.model_validate(reply)

    # --- Runs -----------------------------------------------------------------------------

    def run(self, run_id: int) -> RunView:
        return RunView.model_validate(self._request("GET", f"/v1/runs/{run_id}"))

    def answer(self, run_id: int, by: str, answers: dict[str, str]) -> RunView:
        body = {
            "by": by,
            "answers": [{"question_id": qid, "answer": text} for qid, text in answers.items()],
        }
        return RunView.model_validate(self._request("POST", f"/v1/runs/{run_id}/answers", body))

    def retry(self, run_id: int) -> RunView:
        return RunView.model_validate(self._request("POST", f"/v1/runs/{run_id}/retry"))

    # --- Versions -------------------------------------------------------------------------

    def version(self, version_id: int) -> VersionView:
        return VersionView.model_validate(self._request("GET", f"/v1/versions/{version_id}"))

    def edit(
        self,
        version_id: int,
        by: str,
        tasks: list[TaskEdit],
        assumptions: list[str],
        exclusions: list[str],
    ) -> VersionView:
        body = {
            "by": by,
            "tasks": [task.model_dump() for task in tasks],
            "assumptions": assumptions,
            "exclusions": exclusions,
        }
        reply = self._request("PUT", f"/v1/versions/{version_id}/scope", body)
        return VersionView.model_validate(reply)

    def approve(self, version_id: int, by: str, note: str | None) -> VersionView:
        body = {"by": by, "note": note}
        reply = self._request("POST", f"/v1/versions/{version_id}/approve", body)
        return VersionView.model_validate(reply)

    def send_back(self, version_id: int, by: str, note: str) -> RunView:
        body = {"by": by, "note": note}
        reply = self._request("POST", f"/v1/versions/{version_id}/send-back", body)
        return RunView.model_validate(reply)

    def proposal(self, version_id: int) -> str:
        """The approved proposal's Markdown; the API refuses a draft."""
        try:
            response = self._client.get(f"/v1/versions/{version_id}/proposal.md")
        except httpx.HTTPError as error:
            raise ScopeAPIError(f"The scope API couldn't be reached: {error}") from error
        if response.is_error:
            raise ScopeAPIError(_reason(response))
        return response.text

    # --- Learning -------------------------------------------------------------------------

    def playbook(self) -> PlaybookView:
        return PlaybookView.model_validate(self._request("GET", "/v1/playbook"))

    def activate(self, version: int) -> PlaybookView:
        reply = self._request("POST", f"/v1/playbook/{version}/activate")
        return PlaybookView.model_validate(reply)

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise ScopeAPIError(f"The scope API couldn't be reached: {error}") from error
        if response.is_error:
            raise ScopeAPIError(f"{response.status_code}: {_reason(response)}")
        return response.json()


def _reason(response: httpx.Response) -> str:
    """Why the API refused: FastAPI puts the reason in "detail"."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])  # pyright: ignore[reportUnknownArgumentType]
    return response.text[:200]


@st.cache_resource
def get_api() -> ScopeAPI:
    """One HTTP client for the whole Streamlit server, shared by every session."""
    settings = get_settings()
    key = next(iter(sorted(settings.api_keys)), "")
    client = httpx.Client(base_url=settings.scope_api_url, headers={"X-API-Key": key}, timeout=30)
    return ScopeAPI(client)
