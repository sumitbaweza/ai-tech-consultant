"""Step 5, the consultant (Sonnet 5.5): the questions a senior would ask before quoting.

Clients speak loosely ("make the search better"); developers need exact tasks. The consultant
reads the whole meeting and the requirement list and asks what a senior consultant would ask:
which fields, which filters, how fast, who uses it. Each question carries a default
assumption. The run then waits for a human: answered questions feed the estimator, and every
unanswered one becomes a written assumption in the proposal.
"""

import json

from pydantic import BaseModel, Field

from app.handoffs import FitResult, Question, Requirement
from app.llm import Claude
from app.text import as_data

PROMPT, VERSION = "clarify_requirements", 1
MAX_QUESTIONS = 10


class ProposedQuestion(BaseModel):
    """What the model returns for one question."""

    requirement_id: str | None = Field(description="The requirement it's about, or null")
    question: str = Field(description="Asked the way you would ask the client")
    why_it_matters: str = Field(description="How the answer changes the work or the hours")
    default_assumption: str = Field(description="What we assume if the client doesn't answer")


class Questions(BaseModel):
    questions: list[ProposedQuestion]


def _content(transcript: str, requirements: list[Requirement], fit: list[FitResult]) -> str:
    kinds = {result.requirement_id: result.kind for result in fit}
    listed = [
        {
            "id": r.id,
            "title": r.title,
            "detail": r.detail,
            "is_vague": r.is_vague,
            "conflict": r.conflict,
            "fit": kinds.get(r.id, "unverified"),
        }
        for r in requirements
    ]
    return (
        as_data("transcript", transcript)
        + "\n\nThe requirements found in it:\n\n"
        + as_data("requirements", json.dumps(listed, indent=1))
    )


async def clarify(
    claude: Claude,
    transcript: str,
    requirements: list[Requirement],
    fit: list[FitResult],
    run_tag: str | None = None,
) -> list[Question]:
    if not requirements:
        return []
    reply = await claude.call(
        PROMPT, VERSION, _content(transcript, requirements, fit), Questions, run_tag=run_tag
    )
    known = {r.id for r in requirements}
    return [
        Question(
            id=f"Q{number}",
            # A question about an unknown requirement id is kept, as a question about the project
            requirement_id=proposed.requirement_id if proposed.requirement_id in known else None,
            question=proposed.question,
            why_it_matters=proposed.why_it_matters,
            default_assumption=proposed.default_assumption,
        )
        for number, proposed in enumerate(reply.questions[:MAX_QUESTIONS], start=1)
    ]
