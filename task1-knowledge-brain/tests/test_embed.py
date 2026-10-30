"""The Voyage client against a fake Voyage API: order, batches, retries and the ledger."""

import json
from decimal import Decimal
from typing import Any

import httpx2
import pytest

from app.rag.embed import BATCH_SIZE, EmbeddingError, Voyage
from tests.fakes import ListLedger


def vector(number: int) -> list[float]:
    return [float(number)] + [0.0] * 1023


class FakeVoyageAPI:
    """Answers like Voyage: one vector per input, but listed in reverse order, as the API is
    free to do. `failures` are statuses to answer with first."""

    def __init__(self, failures: list[int] | None = None) -> None:
        self.failures = failures or []
        self.bodies: list[dict[str, Any]] = []
        self.auth: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.auth.append(request.headers["authorization"])
        if self.failures:
            return httpx2.Response(self.failures.pop(0), headers={"retry-after": "3"})
        data = [
            {"object": "embedding", "embedding": vector(index), "index": index}
            for index in range(len(body["input"]))
        ]
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": list(reversed(data)),
                "model": "voyage-4-lite",
                "usage": {"total_tokens": 10_000 * len(body["input"])},
            },
        )


def make_voyage(api: FakeVoyageAPI, ledger: ListLedger, sleeps: list[float]) -> Voyage:
    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(api))
    return Voyage(client, "test-key", ledger, sleep=record_sleep)


async def test_vectors_come_back_in_input_order_and_are_logged() -> None:
    api, ledger = FakeVoyageAPI(), ListLedger()

    vectors = await make_voyage(api, ledger, []).embed(["a", "b", "c"], "document")

    assert [found[0] for found in vectors] == [0.0, 1.0, 2.0]
    [body] = api.bodies
    assert (body["model"], body["input_type"], body["output_dimension"]) == (
        "voyage-4-lite",
        "document",
        1024,
    )
    assert api.auth == ["Bearer test-key"]
    [row] = ledger.rows
    assert (row.prompt, row.model, row.input_tokens, row.success) == (
        "embed_documents",
        "voyage-4-lite",
        30_000,
        True,
    )
    assert row.cost_usd == Decimal("0.000600")  # 30,000 tokens at $0.02 per million


async def test_many_texts_are_sent_in_batches() -> None:
    api = FakeVoyageAPI()

    vectors = await make_voyage(api, ListLedger(), []).embed(["x"] * (BATCH_SIZE + 2), "document")

    assert len(vectors) == BATCH_SIZE + 2
    assert [len(body["input"]) for body in api.bodies] == [BATCH_SIZE, 2]


async def test_a_rate_limit_waits_for_retry_after_then_succeeds() -> None:
    api, ledger = FakeVoyageAPI(failures=[429]), ListLedger()
    sleeps: list[float] = []

    [found] = await make_voyage(api, ledger, sleeps).embed(["question"], "query")

    assert found[0] == 0.0
    assert sleeps == [3.0]  # Retry-After beats the 1 to 2 second backoff
    assert [(row.prompt, row.success) for row in ledger.rows] == [
        ("embed_query", False),
        ("embed_query", True),
    ]


async def test_a_bad_request_fails_at_once() -> None:
    api = FakeVoyageAPI(failures=[400])

    with pytest.raises(EmbeddingError, match="HTTP 400"):
        await make_voyage(api, ListLedger(), []).embed(["x"], "document")

    assert len(api.bodies) == 1
