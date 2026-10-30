"""What the Brain's own clients share. The MCP server and the screens call the API over HTTP,
like any other client, with the first key in BRAIN_API_KEYS."""

from typing import cast

import httpx2

from app.config import Settings


def api_key(settings: Settings) -> str:
    """The key the Brain's own clients send: the first one in BRAIN_API_KEYS."""
    return settings.brain_api_keys.get_secret_value().split(",")[0].strip()


def error_reason(response: httpx2.Response) -> str:
    """Why the API refused: FastAPI puts the reason in "detail"; otherwise, the start of the
    body."""
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and "detail" in body:
        return str(cast(dict[str, object], body)["detail"])
    return response.text[:200]
