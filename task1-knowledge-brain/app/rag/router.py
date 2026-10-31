"""Pick the model that answers a question: Haiku when the question is easy, Sonnet otherwise.

Haiku 5.5 costs a twentieth of Sonnet 5.5, so every question it can answer well is a saving. A
question is easy when both hold:
- search found a close match: the best chunk's cosine similarity is at least HIGH_SIMILARITY.
  Questions that a help-center article answers directly score about 0.60 to 0.66 with
  voyage-4-lite; vaguer or uncovered ones score under 0.57 (measured Oct 31, 2026).
- the question asks for a single fact: one short question, without words that ask for a
  comparison, a reason, advice or several things at once.

Plain rules rather than a classifier call: they cost nothing, add no latency, and every route
can be explained from the question and the search scores alone. ask() then checks Haiku's
answer, and hands the question to Sonnet when Haiku couldn't answer it with confidence
(see app/rag/answer.py). The thresholds are starting points for the eval set to tune.
"""

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from app.rag.search import SearchHit

HIGH_SIMILARITY = 0.60
MAX_SINGLE_FACT_WORDS = 20
# Words that mark a question needing more than one fact: a comparison, a reason, advice, a
# problem to diagnose, or two things joined together
MULTI_PART_WORDS = re.compile(
    r"\b(and|or|versus|vs|compare|comparison|difference|differences|why|should|best|"
    r"recommend|explain|pros|cons|troubleshoot|error|broken|not working)\b",
    re.IGNORECASE,
)

# What the caller asked for: "auto" lets the router decide; the others force one model, which
# is how an eval compares the router with Sonnet alone and Haiku alone
RouteMode = Literal["auto", "sonnet", "haiku"]


class Tier(StrEnum):
    HAIKU = "haiku"
    SONNET = "sonnet"


# The answer prompt each tier runs: the same instructions, on a different model
PROMPTS = {Tier.HAIKU: "answer_fast", Tier.SONNET: "answer"}


class Route(BaseModel):
    """Which model answered and why, returned with every answer so evals can measure it."""

    mode: RouteMode
    tier: Tier  # the model that gave the final answer
    prompt: str  # its prompt file, as named in llm_calls
    reason: str
    top_similarity: float | None
    single_fact: bool
    # True when Haiku tried first and Sonnet answered instead
    escalated: bool = False


def choose_route(question: str, hits: list[SearchHit], mode: RouteMode = "auto") -> Route:
    top = max((hit.similarity for hit in hits if hit.similarity is not None), default=None)
    single = is_single_fact(question)
    if mode != "auto":
        tier = Tier(mode)
        return _route(mode, tier, f"forced to {tier.value} by the caller", top, single)
    if top is None:
        return _route(
            mode, Tier.SONNET, "no similarity score (exact-word search only)", top, single
        )
    if top < HIGH_SIMILARITY:
        reason = f"best match {top:.2f} is under {HIGH_SIMILARITY:.2f}"
        return _route(mode, Tier.SONNET, reason, top, single)
    if not single:
        return _route(mode, Tier.SONNET, "the question asks for more than one fact", top, single)
    reason = f"single-fact question with a close match ({top:.2f})"
    return _route(mode, Tier.HAIKU, reason, top, single)


def is_single_fact(question: str) -> bool:
    """One short question that doesn't ask for a comparison, a reason, advice or two things."""
    text = question.strip()
    # Several questions, or several sentences: "?" or ". " before the end
    if text.rstrip("?.! ").count("?") > 0 or re.search(r"[.!;]\s+\S", text):
        return False
    if len(text.split()) > MAX_SINGLE_FACT_WORDS:
        return False
    return MULTI_PART_WORDS.search(text) is None


def escalate(route: Route, why: str) -> Route:
    """Sonnet takes over a question Haiku couldn't answer well."""
    return route.model_copy(
        update={
            "tier": Tier.SONNET,
            "prompt": PROMPTS[Tier.SONNET],
            "reason": f"{route.reason}; escalated to sonnet: {why}",
            "escalated": True,
        }
    )


def _route(mode: RouteMode, tier: Tier, reason: str, top: float | None, single: bool) -> Route:
    return Route(
        mode=mode,
        tier=tier,
        prompt=PROMPTS[tier],
        reason=reason,
        top_similarity=round(top, 4) if top is not None else None,
        single_fact=single,
    )
