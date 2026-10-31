"""Step 4, the fit checker (Sonnet 5.5 + the Knowledge Brain): setting, small change or code?

This is where the client saves money: a request the product already handles with a setting
costs an hour of configuration, not days of custom code. For each requirement:

1. Search the Brain (POST /v1/search, no LLM, free) for the closest help-center chunks.
2. Sonnet decides from those chunks: setting, small change, or custom code. It must cite the
   chunks it relied on; code drops any chunk id that search didn't return, and a "setting"
   with no real evidence becomes "unverified".
3. For a setting, ask the Brain (POST /v1/ask) how to turn it on, for cited steps the proposal
   can show. Only settings are asked about: the Brain sends every question it can't answer to
   its expert queue, and "build a custom CRM" would only add noise there.

If the Brain is down or knows nothing yet, the result is "unverified" and the run goes on;
the estimator prices unverified items as custom work, and the proposal says they weren't
checked.
"""

import asyncio
import logging

from pydantic import BaseModel, Field

from app.brain_client import Brain, BrainUnavailable, SearchHit
from app.handoffs import ArticleLink, FitKind, FitResult, Requirement
from app.llm import Claude, LLMError
from app.text import as_data

logger = logging.getLogger(__name__)

PROMPT, VERSION = "check_fit", 1
SEARCH_LIMIT = 6


class FitDecision(BaseModel):
    """What the model returns."""

    kind: FitKind = Field(description="setting, small_change or custom")
    reason: str = Field(description="One or two sentences, naming the setting if there is one")
    setting_path: str | None = Field(
        description="Where the setting is, as the help center names it, e.g. 'Settings > X'"
    )
    evidence_chunk_ids: list[int] = Field(description="The help-center chunks this rests on")


def _content(requirement: Requirement, hits: list[SearchHit]) -> str:
    request = f"{requirement.title}: {requirement.detail}\nClient's words: " + " / ".join(
        f'"{quote.text}"' for quote in requirement.quotes
    )
    sources = "\n\n---\n\n".join(
        f"Chunk {hit.chunk_id} ({hit.kind}) from '{hit.title or 'untitled'}' ({hit.url}):\n"
        f"{hit.content}"
        for hit in hits
    )
    return (
        as_data("client_request", request)
        + "\n\nThe help-center chunks closest to it:\n\n"
        + as_data("help_center", sources)
    )


def _setting_question(requirement: Requirement) -> str:
    return (
        "How do I set this up with the existing settings? "
        f"{requirement.title}: {requirement.detail}"
    )


async def check_one(
    claude: Claude, brain: Brain, requirement: Requirement, run_tag: str | None = None
) -> FitResult:
    try:
        hits = await brain.search(f"{requirement.title}. {requirement.detail}", SEARCH_LIMIT)
    except BrainUnavailable:
        return FitResult(
            requirement_id=requirement.id,
            kind="unverified",
            reason="The Knowledge Brain couldn't be reached, so this wasn't checked "
            "against the product's settings.",
            brain="unavailable",
        )
    if not hits:
        return FitResult(
            requirement_id=requirement.id,
            kind="unverified",
            reason="The help center has nothing close to this request.",
            brain="no_match",
        )

    try:
        decision = await claude.call(
            PROMPT, VERSION, _content(requirement, hits), FitDecision, run_tag=run_tag
        )
    except LLMError as error:
        logger.warning("Fit check for %s failed: %s", requirement.id, error)
        return FitResult(
            requirement_id=requirement.id,
            kind="unverified",
            reason="The fit check failed, so this wasn't checked against the settings.",
            brain="error",
        )

    by_id = {hit.chunk_id: hit for hit in hits}
    evidence = [by_id[i] for i in dict.fromkeys(decision.evidence_chunk_ids) if i in by_id]
    articles = list(
        {hit.url: ArticleLink(title=hit.title, url=hit.url) for hit in evidence}.values()
    )
    kind, reason = decision.kind, decision.reason
    if kind in ("setting", "small_change") and not evidence:
        kind = "unverified"
        reason = f"{reason} (No help-center chunk backs this up, so it's treated as unverified.)"

    how_to: str | None = None
    if kind == "setting":
        try:
            answer = await brain.ask(_setting_question(requirement))
        except BrainUnavailable:
            answer = None
        if answer is not None and answer.answered:
            how_to = answer.answer
            for citation in answer.citations:
                if all(link.url != citation.url for link in articles):
                    articles.append(ArticleLink(title=citation.title, url=citation.url))

    return FitResult(
        requirement_id=requirement.id,
        kind=kind,
        reason=reason,
        setting_path=decision.setting_path if kind != "custom" else None,
        how_to=how_to,
        articles=articles,
        brain="ok",
    )


async def check_fit(
    claude: Claude,
    brain: Brain,
    requirements: list[Requirement],
    concurrency: int,
    run_tag: str | None = None,
) -> list[FitResult]:
    """A FitResult for every requirement, in the same order, `concurrency` at a time."""
    limit = asyncio.Semaphore(concurrency)

    async def one(requirement: Requirement) -> FitResult:
        async with limit:
            return await check_one(claude, brain, requirement, run_tag)

    return list(await asyncio.gather(*(one(r) for r in requirements)))
