"""Can the product already do what a client asks for? Yes, partly or no, with the setting and the
articles that show it.

check_capability() runs these steps:
1. Hybrid search finds the MAX_SOURCES chunks closest to the requirement.
2. If none is close in meaning (the same MIN_SIMILARITY bar as ask()), the help center doesn't
   cover it: the verdict is "unknown", and Claude is never called.
3. Sonnet reads those chunks and gives a verdict, the setting's name and citations
   (prompts/check_capability).
4. Citation ids that don't match a source are dropped. A verdict other than "unknown" with no
   valid citation left can't be checked, so it becomes "unknown".

Task 3's fit checker can use it (or MCP's check_capability) to price a requirement as
configuration rather than custom code.
"""

import logging
from typing import Literal

import anthropic
from anthropic.types.beta import BetaContentBlockParam
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import Claude, LLMError
from app.rag.answer import (
    MAX_SOURCES,
    MIN_SIMILARITY,
    Citation,
    Source,
    best_similarity,
    make_citation,
    make_source,
    sources_block,
    valid_citations,
)
from app.rag.embed import Embedder, EmbeddingError
from app.rag.search import search

logger = logging.getLogger(__name__)

Verdict = Literal["yes", "partly", "no", "unknown"]
# How the verdict was reached: checked by Claude, or why not
CheckOutcome = Literal["checked", "low_retrieval", "no_valid_citation", "model_failed"]


class CapabilityDraft(BaseModel):
    """What Sonnet returns for prompts/check_capability."""

    verdict: Verdict
    setting: str | None
    explanation: str
    citations: list[str]  # source ids, such as "S2"
    confidence: float = Field(ge=0, le=1)


class CapabilityResult(BaseModel):
    requirement: str
    verdict: Verdict
    setting: str | None  # where the feature lives, as the help center names it
    explanation: str
    citations: list[Citation]
    confidence: float
    outcome: CheckOutcome
    sources: list[Source]  # everything search found, best first


NOT_COVERED = "The help center doesn't describe a setting for this, so it can't be confirmed."
FAILED = "The check couldn't run just now. Please try again."


async def check_capability(
    session: AsyncSession,
    claude: Claude,
    embedder: Embedder | None,
    requirement: str,
    *,
    run_tag: str | None = None,
) -> CapabilityResult:
    query_vector: list[float] | None = None
    if embedder is not None:
        try:
            [query_vector] = await embedder.embed([requirement], "query")
        except EmbeddingError:
            logger.warning("Embedding the requirement failed; searching by exact words only")
    hits = await search(session, requirement, query_vector, limit=MAX_SOURCES)
    found = [make_source(hit) for hit in hits]

    if not hits or (query_vector is not None and best_similarity(hits) < MIN_SIMILARITY):
        return _unknown(requirement, "low_retrieval", NOT_COVERED, found)

    sources = {f"S{number}": hit for number, hit in enumerate(hits, start=1)}
    try:
        draft = await claude.call(
            "check_capability",
            1,
            capability_content(requirement, sources_block(sources)),
            CapabilityDraft,
            run_tag=run_tag,
        )
    except (LLMError, anthropic.APIError) as error:
        logger.warning("Checking a capability failed: %s", error)
        return _unknown(requirement, "model_failed", FAILED, found)

    cited = valid_citations(draft.citations, sources)
    if draft.verdict == "unknown":
        return _unknown(requirement, "checked", draft.explanation, found)
    if not cited:
        return _unknown(requirement, "no_valid_citation", NOT_COVERED, found)
    return CapabilityResult(
        requirement=requirement,
        verdict=draft.verdict,
        setting=draft.setting if draft.verdict != "no" else None,
        explanation=draft.explanation,
        citations=[make_citation(source_id, sources[source_id]) for source_id in cited],
        confidence=draft.confidence,
        outcome="checked",
        sources=found,
    )


def capability_content(requirement: str, sources: str) -> list[BetaContentBlockParam]:
    """The sources, then the requirement, each in tags that mark them as data."""
    return [{"type": "text", "text": f"{sources}\n\n<requirement>{requirement}</requirement>"}]


def _unknown(
    requirement: str, outcome: CheckOutcome, explanation: str, found: list[Source]
) -> CapabilityResult:
    return CapabilityResult(
        requirement=requirement,
        verdict="unknown",
        setting=None,
        explanation=explanation,
        citations=[],
        confidence=0.0,
        outcome=outcome,
        sources=found,
    )
