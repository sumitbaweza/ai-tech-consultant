"""The screens' client for the Brain's API.

The screens are one more client of the API, like the MCP server. Each response is read with the
API's own Pydantic model, so a change to the API that breaks a screen fails here, loudly.
"""

from typing import Any

import httpx2
import streamlit as st

from app.api_client import api_key, error_reason
from app.config import get_settings
from app.progress import Stats


class BrainError(Exception):
    """The API refused, or couldn't be reached. The message is meant for the screen."""


class Brain:
    def __init__(self, client: httpx2.Client) -> None:
        self._client = client

    def stats(self) -> Stats:
        """The dashboard's numbers, and the latest run."""
        return Stats.model_validate(self._request("GET", "/v1/stats"))

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=body)
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
