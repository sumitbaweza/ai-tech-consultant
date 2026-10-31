"""A fake Claude API and a fake Knowledge Brain: no network, no keys, no cost.

The Anthropic SDK sends its requests through httpx2, so the fake Claude is an httpx2
MockTransport handler. It tells the prompts apart by their system text and answers each with
a scripted reply. The Brain client uses httpx, so the fake Brain is a respx router.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import httpx2
import respx
from anthropic import AsyncAnthropic

from app.brain_client import Brain
from app.llm import PROMPTS_DIR, Claude, load_prompt
from app.models import LLMCall

type Reply = dict[str, Any] | Callable[[str], dict[str, Any]]

PROMPT_NAMES = sorted(path.name for path in PROMPTS_DIR.iterdir() if path.is_dir())


class ListLedger:
    """Keeps ledger rows in a list instead of the database."""

    def __init__(self) -> None:
        self.rows: list[LLMCall] = []

    async def record(self, call: LLMCall) -> None:
        self.rows.append(call)


def message_json(
    text: str, *, model: str = "claude-sonnet-5-5", stop_reason: str = "end_turn"
) -> dict[str, Any]:
    """A Messages API reply whose one text block is `text`."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def user_text(body: dict[str, Any]) -> str:
    """Everything the request sends as the user's turn, as one string."""
    content = body["messages"][0]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if block.get("type") == "text")


class FakeClaudeAPI:
    """Answers each prompt with its scripted reply: a fixed object, a function of the user's
    text, or a list used one item per call. Records every request it gets."""

    def __init__(self, replies: dict[str, Reply | list[Reply]]) -> None:
        self.replies = replies
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self._by_system = {load_prompt(name, 1).system: name for name in PROMPT_NAMES}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body: dict[str, Any] = json.loads(request.content)
        name = self._by_system[body["system"]]
        self.requests.append((name, body))
        scripted = self.replies[name]
        reply: Reply
        if isinstance(scripted, list):  # one item per call, the last one repeated
            replies = cast(list[Reply], scripted)
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
        else:
            reply = scripted
        data: dict[str, Any] = reply(user_text(body)) if callable(reply) else reply
        return httpx2.Response(200, json=message_json(json.dumps(data), model=body["model"]))

    def calls(self, name: str) -> list[dict[str, Any]]:
        return [body for prompt, body in self.requests if prompt == name]


def make_claude(
    handler: Callable[[httpx2.Request], httpx2.Response],
    ledger: ListLedger | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> Claude:
    # max_retries=0: the SDK's own retries are its business; the tests check ours
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return Claude(client, ledger or ListLedger(), prompts_dir)


# --- The fake Brain -----------------------------------------------------------------------

BRAIN_URL = "http://brain.test"


def hit(chunk_id: int, title: str, content: str) -> dict[str, Any]:
    """One /v1/search hit, with every field the real API returns."""
    return {
        "chunk_id": chunk_id,
        "page_id": chunk_id * 10,
        "page_kind": "article",
        "kind": "text",
        "title": title,
        "url": f"https://support.example.com/article/{chunk_id}",
        "image_url": None,
        "content": content,
        "score": 0.9,
        "similarity": 0.8,
    }


def search_reply(*hits: dict[str, Any]) -> dict[str, Any]:
    return {"query": "q", "mode": "hybrid", "hits": list(hits)}


def ask_reply(answer: str, *, answered: bool = True) -> dict[str, Any]:
    return {
        "question": "q",
        "standalone_question": "q",
        "answered": answered,
        "answer": answer,
        "citations": [
            {
                "source_id": "S1",
                "origin": "article",
                "chunk_id": 11,
                "page_id": 110,
                "title": "Member approval",
                "url": "https://support.example.com/article/11",
                "image_url": None,
                "snippet": "...",
            }
        ]
        if answered
        else [],
        "confidence": 0.9 if answered else 0.1,
        "outcome": "answered" if answered else "not_in_sources",
        "sources": [],
        "expert_task_id": None,
    }


def make_brain() -> Brain:
    return Brain(httpx.AsyncClient(base_url=BRAIN_URL, headers={"X-API-Key": "test-brain-key"}))


def brain_routes(
    router: respx.Router, search: dict[str, Any], ask: dict[str, Any]
) -> tuple[respx.Route, respx.Route]:
    """Mock /v1/search and /v1/ask; returns both routes, to check which were called."""
    return (
        router.post(f"{BRAIN_URL}/v1/search").mock(return_value=httpx.Response(200, json=search)),
        router.post(f"{BRAIN_URL}/v1/ask").mock(return_value=httpx.Response(200, json=ask)),
    )
