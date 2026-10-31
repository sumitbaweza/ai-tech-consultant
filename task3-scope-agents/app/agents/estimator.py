"""Step 6, the estimator (Sonnet 5.5): tasks with low, likely and high hours.

Sonnet turns the requirements into developer tasks, using the fit checker's verdicts (a
setting is 0.5 to 1 hour of configuration, not custom code) and the consultant's answered
questions and assumptions. On a second round it also gets the reviewer's issues, or a human's
note when the approval screen sent the scope back, plus its previous tasks to correct.

The model only gives hours. Code checks them (low <= likely <= high, known requirement ids),
prices each task from the rate card in config, and keeps the model's own hours in
`ai_hours`, so later human edits can be measured against them.
"""

import json

from pydantic import BaseModel, Field

from app.handoffs import (
    EstimationRule,
    FitResult,
    Hours,
    Question,
    Requirement,
    Review,
    Role,
    Task,
)
from app.llm import Claude
from app.text import as_data

PROMPT, VERSION = "estimate_tasks", 2
# What the model sees of its previous tasks on a second round
PREVIOUS_FIELDS = {"id", "requirement_ids", "title", "role", "low", "likely", "high", "basis"}


class EstimatedTask(BaseModel):
    """What the model returns for one task."""

    requirement_ids: list[str] = Field(description="The requirements this task delivers")
    title: str
    description: str = Field(description="What a developer does, specific enough to build")
    role: Role
    low: float = Field(description="Hours if all goes well")
    likely: float = Field(description="The most likely hours")
    high: float = Field(description="Hours if it goes badly")
    basis: str = Field(description="Why these hours, in one sentence")


class Estimate(BaseModel):
    tasks: list[EstimatedTask]
    assumptions: list[str] = Field(description="What the estimate takes for granted")
    exclusions: list[str] = Field(description="What is clearly not included")


class EstimateResult(BaseModel):
    tasks: list[Task]
    assumptions: list[str]
    exclusions: list[str]


def _content(
    requirements: list[Requirement],
    fit: list[FitResult],
    questions: list[Question],
    feedback: Review | None,
    human_note: str | None,
    previous: list[Task] | None,
    rules: list[EstimationRule],
) -> str:
    fit_by_id = {result.requirement_id: result for result in fit}
    listed = [
        {
            "id": r.id,
            "title": r.title,
            "detail": r.detail,
            "priority": r.priority,
            "fit": fit_by_id[r.id].kind if r.id in fit_by_id else "unverified",
            "fit_reason": fit_by_id[r.id].reason if r.id in fit_by_id else None,
            "setting_path": fit_by_id[r.id].setting_path if r.id in fit_by_id else None,
        }
        for r in requirements
    ]
    answers = [
        {"requirement_id": q.requirement_id, "question": q.question, "resolution": q.resolution}
        for q in questions
    ]
    parts = [
        "Requirements, with whether the product already has a setting for each:\n\n"
        + as_data("requirements", json.dumps(listed, indent=1)),
        "Clarifying questions, answered by the client or assumed:\n\n"
        + as_data("clarifications", json.dumps(answers, indent=1)),
    ]
    if rules:
        playbook = [rule.model_dump() for rule in rules]
        parts.append(
            "The estimation playbook, learned from human corrections:\n\n"
            + as_data("playbook", json.dumps(playbook, indent=1))
        )
    if previous:
        tasks = [task.model_dump(include=PREVIOUS_FIELDS) for task in previous]
        parts.append("Your previous estimate, to correct:\n\n" + json.dumps(tasks, indent=1))
    if feedback is not None:
        parts.append(
            "The senior reviewer sent it back. Fix every issue:\n\n"
            + json.dumps(feedback.model_dump(), indent=1)
        )
    if human_note:
        parts.append(
            "A human reviewer sent it back with this note:\n\n" + as_data("note", human_note)
        )
    return "\n\n".join(parts)


def to_task(
    number: int, estimated: EstimatedTask, known: set[str], rates: dict[str, float]
) -> Task:
    """Check one model task and price it."""
    low, likely, high = sorted(
        max(0.0, hours) for hours in (estimated.low, estimated.likely, estimated.high)
    )
    hours = Hours(low=low, likely=likely, high=high)
    return Task(
        id=f"T{number}",
        requirement_ids=[i for i in dict.fromkeys(estimated.requirement_ids) if i in known],
        title=estimated.title,
        description=estimated.description,
        role=estimated.role,
        low=low,
        likely=likely,
        high=high,
        rate=rates.get(estimated.role, rates.get("developer", 0.0)),
        basis=estimated.basis,
        ai_hours=hours,
    )


async def estimate(
    claude: Claude,
    requirements: list[Requirement],
    fit: list[FitResult],
    questions: list[Question],
    rates: dict[str, float],
    *,
    feedback: Review | None = None,
    human_note: str | None = None,
    previous: list[Task] | None = None,
    rules: list[EstimationRule] | None = None,
    run_tag: str | None = None,
) -> EstimateResult:
    content = _content(requirements, fit, questions, feedback, human_note, previous, rules or [])
    reply = await claude.call(PROMPT, VERSION, content, Estimate, run_tag=run_tag)
    known = {r.id for r in requirements}
    return EstimateResult(
        tasks=[to_task(n, t, known, rates) for n, t in enumerate(reply.tasks, start=1)],
        assumptions=reply.assumptions,
        exclusions=reply.exclusions,
    )
