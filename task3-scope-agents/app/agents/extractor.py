"""Step 2, the extractor (Haiku 5.5, one call per chunk, run in parallel).

Haiku reads one chunk and lists what the client asks for, each with the client's exact words.
Then code checks every quote against the chunk: a requirement whose quote isn't in the
transcript is dropped and reported, never passed on. That check stops invented requirements.

The transcript is data, never instructions: it goes inside <transcript> tags that a speaker
can't close (app.text.as_data), the prompt says so, and every requirement keeps its quote,
speaker and time, so a human can see where each one came from.
"""

import asyncio

from pydantic import BaseModel, Field

from app.handoffs import Chunk, ExtractedItem, Priority, Quote
from app.llm import Claude
from app.text import as_data, contains_quote

PROMPT, VERSION = "extract_requirements", 1


class ExtractedRequirement(BaseModel):
    """What the model returns for one requirement."""

    title: str = Field(description="A short name for the request, under 10 words")
    detail: str = Field(description="What the client wants, in one or two plain sentences")
    quote: str = Field(description="The client's exact words, copied from the transcript")
    priority: Priority
    is_vague: bool = Field(description="True when a developer couldn't build it without asking")


class Extraction(BaseModel):
    requirements: list[ExtractedRequirement]


class DroppedItem(BaseModel):
    """A requirement the model returned whose quote isn't in the transcript."""

    chunk_index: int
    title: str
    quote: str


class ExtractionResult(BaseModel):
    items: list[ExtractedItem]
    dropped: list[DroppedItem]


def _content(chunk: Chunk, total: int) -> str:
    span = f", from {chunk.start} to {chunk.end}" if chunk.start and chunk.end else ""
    return f"Part {chunk.index + 1} of {total} of the meeting{span}.\n\n" + as_data(
        "transcript", chunk.text
    )


def _find_quote(chunk: Chunk, quote: str) -> Quote | None:
    """Who said `quote` and when: the first turn of the chunk that contains it."""
    for turn in chunk.turns:
        if contains_quote(turn.text, quote):
            return Quote(text=quote, speaker=turn.speaker, time=turn.time)
    return None


async def extract_chunk(
    claude: Claude, chunk: Chunk, total: int, run_tag: str | None = None
) -> ExtractionResult:
    reply = await claude.call(PROMPT, VERSION, _content(chunk, total), Extraction, run_tag=run_tag)
    items: list[ExtractedItem] = []
    dropped: list[DroppedItem] = []
    for found in reply.requirements:
        quote = _find_quote(chunk, found.quote)
        if quote is None:
            dropped.append(
                DroppedItem(chunk_index=chunk.index, title=found.title, quote=found.quote)
            )
            continue
        items.append(
            ExtractedItem(
                id=f"C{chunk.index + 1}-{len(items) + 1}",
                chunk_index=chunk.index,
                title=found.title,
                detail=found.detail,
                quote=quote,
                priority=found.priority,
                is_vague=found.is_vague,
            )
        )
    return ExtractionResult(items=items, dropped=dropped)


async def extract(
    claude: Claude, chunks: list[Chunk], concurrency: int, run_tag: str | None = None
) -> ExtractionResult:
    """Every chunk at once, `concurrency` calls at a time. If any chunk fails, the whole step
    fails: a scope missing a part of the meeting is worse than a clear error and a retry."""
    limit = asyncio.Semaphore(concurrency)

    async def one(chunk: Chunk) -> ExtractionResult:
        async with limit:
            return await extract_chunk(claude, chunk, len(chunks), run_tag)

    results = await asyncio.gather(*(one(chunk) for chunk in chunks))
    return ExtractionResult(
        items=[item for result in results for item in result.items],
        dropped=[item for result in results for item in result.dropped],
    )
