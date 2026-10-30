"""Embeddings from Voyage AI, the provider Claude's documentation recommends.

The Brain calls Voyage's REST API directly: one POST per batch of texts. Its SDK would add numpy
and a tokenizer for that single call. Each request writes a row to brain.llm_calls, like a
Claude call, so embedding costs appear in the same ledger.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from functools import lru_cache
from typing import Literal, Protocol

import httpx2
from pydantic import BaseModel

from app.config import get_settings
from app.crawler.fetch import RETRY_STATUSES, backoff, parse_retry_after
from app.db import SessionFactory
from app.llm import DatabaseLedger, Ledger
from app.models import EMBEDDING_DIMENSIONS, LLMCall

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
EMBEDDING_MODEL = "voyage-4-lite"
# voyage-4-lite: $0.02 per million tokens, with the first 200M free (checked Oct 30, 2026)
PRICE_PER_MILLION_TOKENS = Decimal("0.02")
BATCH_SIZE = 128  # texts per request; Voyage takes up to 1,000 texts and 1M tokens
MAX_ATTEMPTS = 5

InputType = Literal["document", "query"]
LEDGER_NAMES: dict[InputType, str] = {"document": "embed_documents", "query": "embed_query"}


class EmbeddingError(Exception):
    """Voyage couldn't embed the texts, even after retries."""


class Embedder(Protocol):
    """What indexing and search need from an embedding model; tests pass a fake."""

    model: str

    async def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...


class _Embedding(BaseModel):
    embedding: list[float]
    index: int


class _Usage(BaseModel):
    total_tokens: int


class _Response(BaseModel):
    data: list[_Embedding]
    usage: _Usage


class Voyage:
    def __init__(
        self,
        client: httpx2.AsyncClient,
        api_key: str,
        ledger: Ledger,
        *,
        model: str = EMBEDDING_MODEL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model = model
        self._client = client
        self._api_key = api_key
        self._ledger = ledger
        self._sleep = sleep

    async def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        """One vector per text, in the same order. Voyage embeds documents and queries a little
        differently, so the caller says which these are."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(await self._embed_batch(texts[start : start + BATCH_SIZE], input_type))
        return vectors

    async def _embed_batch(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        body = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": EMBEDDING_DIMENSIONS,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        # Same retry rule as the crawler: 429 and 5xx and network errors, waiting as long as
        # Retry-After asks, or else backing off exponentially
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                response = await self._client.post(VOYAGE_URL, json=body, headers=headers)
            except httpx2.TransportError as error:
                await self._record(input_type, 0, started, error=f"Network error: {error}")
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingError(f"Network error after {attempt} tries: {error}") from error
                await self._sleep(backoff(attempt))
                continue
            if response.status_code in RETRY_STATUSES:
                await self._record(input_type, 0, started, error=f"HTTP {response.status_code}")
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingError(f"HTTP {response.status_code} after {attempt} tries")
                wait = parse_retry_after(response.headers.get("retry-after")) or 0.0
                await self._sleep(max(backoff(attempt), wait))
                continue
            if response.status_code != 200:
                error = f"HTTP {response.status_code}: {response.text[:200]}"
                await self._record(input_type, 0, started, error=error)
                raise EmbeddingError(error)

            parsed = _Response.model_validate_json(response.content)
            await self._record(input_type, parsed.usage.total_tokens, started)
            # Voyage gives each vector's position in the input; don't rely on the list order
            return [item.embedding for item in sorted(parsed.data, key=lambda item: item.index)]
        raise AssertionError("unreachable: the last attempt returns or raises")

    async def _record(
        self, input_type: InputType, tokens: int, started: float, error: str | None = None
    ) -> None:
        cost = (tokens * PRICE_PER_MILLION_TOKENS / 1_000_000).quantize(Decimal("0.000001"))
        await self._ledger.record(
            LLMCall(
                prompt=LEDGER_NAMES[input_type],
                prompt_version=None,
                model=self.model,
                input_tokens=tokens,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=cost,
                latency_ms=round((time.perf_counter() - started) * 1000),
                is_batch=False,
                success=error is None,
                error=error,
            )
        )


@lru_cache
def get_voyage() -> Voyage | None:
    """The app's shared Voyage client, or None when VOYAGE_API_KEY isn't set."""
    api_key = get_settings().voyage_api_key.get_secret_value()
    if not api_key:
        return None
    client = httpx2.AsyncClient(timeout=httpx2.Timeout(60.0))
    return Voyage(client, api_key, DatabaseLedger(SessionFactory))
