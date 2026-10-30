"""Shared test helpers: a Claude client that talks to a fake API, and a ledger kept in a list.

The SDK sends its requests through httpx2, so a fake API is just an httpx2 MockTransport
handler: it records each request and answers with a canned reply. No network, key or cost.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
from anthropic import AsyncAnthropic

from app.llm import PROMPTS_DIR, Claude
from app.models import LLMCall

type Handler = Callable[[httpx2.Request], httpx2.Response]


class ListLedger:
    """Keeps ledger rows in a list instead of the database."""

    def __init__(self) -> None:
        self.rows: list[LLMCall] = []

    async def record(self, call: LLMCall) -> None:
        self.rows.append(call)


def make_claude(
    handler: Handler, prompts_dir: Path = PROMPTS_DIR, ledger: ListLedger | None = None
) -> Claude:
    # max_retries=0: the SDK's own retries are its business; the tests check ours
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return Claude(client, ledger or ListLedger(), prompts_dir)


def message_json(
    text: str,
    *,
    model: str = "claude-haiku-5-5",
    stop_reason: str = "end_turn",
    input_tokens: int = 1000,
    output_tokens: int = 200,
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
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def batch_json(status: str) -> dict[str, Any]:
    """A Message Batch as the API describes it, either still running or ended."""
    ended = status == "ended"
    return {
        "id": "msgbatch_test",
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 0 if ended else 2,
            "succeeded": 1 if ended else 0,
            "errored": 1 if ended else 0,
            "canceled": 0,
            "expired": 0,
        },
        "created_at": "2026-10-30T10:00:00Z",
        "expires_at": "2026-10-31T10:00:00Z",
        "ended_at": "2026-10-30T10:05:00Z" if ended else None,
        "archived_at": None,
        "cancel_initiated_at": None,
        "results_url": (
            "https://api.anthropic.com/v1/messages/batches/msgbatch_test/results" if ended else None
        ),
    }
