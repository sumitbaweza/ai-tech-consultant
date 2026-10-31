"""Step 3, the merger (Sonnet 5.5): one clean requirement list from every chunk's items.

The same request often comes up several times in a meeting, and the chunk overlap repeats a
turn on purpose. Sonnet groups the items that mean the same thing and flags a conflict when the
client said different things at different times ("3 categories" early on, "5" later).

Code then checks the grouping: an id the model made up is ignored, and an item the model left
out is kept as a requirement of its own. So the merger can combine requirements but never
lose one, and every requirement keeps at least one real quote.
"""

import json

from pydantic import BaseModel, Field

from app.handoffs import ExtractedItem, Priority, Quote, Requirement
from app.llm import Claude
from app.text import as_data, normalize

PROMPT, VERSION = "merge_requirements", 1


class MergedRequirement(BaseModel):
    """What the model returns for one merged requirement."""

    title: str
    detail: str = Field(description="Everything the client said about it, combined")
    priority: Priority
    is_vague: bool
    source_ids: list[str] = Field(description="The ids of every item merged into this one")
    conflict: str | None = Field(
        description="If the client said different things about it, what and when; else null"
    )


class Merge(BaseModel):
    requirements: list[MergedRequirement]


def _content(items: list[ExtractedItem]) -> str:
    listed = [
        {
            "id": item.id,
            "title": item.title,
            "detail": item.detail,
            "quote": item.quote.text,
            "speaker": item.quote.speaker,
            "time": item.quote.time,
            "priority": item.priority,
            "is_vague": item.is_vague,
        }
        for item in items
    ]
    return "Requirements found in each part of the meeting:\n\n" + as_data(
        "requirements", json.dumps(listed, indent=1)
    )


def _quotes(sources: list[ExtractedItem]) -> list[Quote]:
    """Each distinct quote once, in the order they were said."""
    seen: set[str] = set()
    quotes: list[Quote] = []
    for item in sources:
        key = normalize(item.quote.text)
        if key not in seen:
            seen.add(key)
            quotes.append(item.quote)
    return quotes


async def merge(
    claude: Claude, items: list[ExtractedItem], prefix: str = "R", run_tag: str | None = None
) -> list[Requirement]:
    """Merged requirements, numbered `prefix`1, `prefix`2, ... (R for a first meeting, M for a
    follow-up, so the two lists can't be confused)."""
    if not items:
        return []
    reply = await claude.call(PROMPT, VERSION, _content(items), Merge, run_tag=run_tag)

    by_id = {item.id: item for item in items}
    used: set[str] = set()
    requirements: list[Requirement] = []

    def add(merged: MergedRequirement, sources: list[ExtractedItem]) -> None:
        requirements.append(
            Requirement(
                id=f"{prefix}{len(requirements) + 1}",
                title=merged.title,
                detail=merged.detail,
                priority=merged.priority,
                is_vague=merged.is_vague,
                quotes=_quotes(sources),
                source_ids=[item.id for item in sources],
                conflict=merged.conflict,
            )
        )

    for merged in reply.requirements:
        # Ignore ids the model invented, and items already placed in an earlier group
        sources = [
            by_id[i] for i in dict.fromkeys(merged.source_ids) if i in by_id and i not in used
        ]
        if not sources:
            continue
        used.update(item.id for item in sources)
        add(merged, sources)

    for item in items:
        if item.id not in used:  # the model left it out: keep it rather than lose it
            kept = MergedRequirement(
                title=item.title,
                detail=item.detail,
                priority=item.priority,
                is_vague=item.is_vague,
                source_ids=[item.id],
                conflict=None,
            )
            add(kept, [item])
    return requirements
