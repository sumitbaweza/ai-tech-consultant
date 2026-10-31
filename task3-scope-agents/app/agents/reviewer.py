"""Step 7, the reviewer (Opus 5.5): a senior checks the estimate and can send it back.

Two layers. First, code checks the joins, which need no model and are never wrong: every
requirement has a task, every task serves a requirement, and no setting is priced like custom
code. Then Opus reads the whole meeting and the estimate, the way a senior would, for missed
requests, hours that look off, and risky assumptions.

The verdict is "send_back" when Opus says so or when code found a gap. The orchestrator then
runs the estimator again with these issues, at most `max_review_rounds` times; after that the
remaining issues go to the human on the approval screen.
"""

import json

from app.handoffs import Coverage, FitResult, Question, Requirement, Review, ReviewIssue, Task
from app.llm import Claude, Content, cached_text
from app.text import as_data

PROMPT, VERSION = "review_scope", 1
# Configuring an existing setting shouldn't take longer than this
SETTING_MAX_LIKELY_HOURS = 2.0
# What the reviewer sees of each requirement and task
REQUIREMENT_FIELDS = {"id", "title", "detail", "priority"}
TASK_FIELDS = {"id", "requirement_ids", "title", "description", "role", "low", "likely", "high"}


def coverage(requirements: list[Requirement], fit: list[FitResult], tasks: list[Task]) -> Coverage:
    """The checks code can make: requirement <-> task joins, and settings priced as settings."""
    covered = {rid for task in tasks for rid in task.requirement_ids}
    settings = {result.requirement_id for result in fit if result.kind == "setting"}
    setting_hours: dict[str, float] = {}
    for task in tasks:
        for rid in task.requirement_ids:
            if rid in settings:
                setting_hours[rid] = setting_hours.get(rid, 0.0) + task.likely
    return Coverage(
        uncovered_requirements=[r.id for r in requirements if r.id not in covered],
        orphan_tasks=[task.id for task in tasks if not task.requirement_ids],
        settings_priced_as_custom=[
            rid for rid, hours in setting_hours.items() if hours > SETTING_MAX_LIKELY_HOURS
        ],
    )


def coverage_issues(report: Coverage) -> list[ReviewIssue]:
    """The code's findings, written as review issues the estimator can act on."""
    issues = [
        ReviewIssue(target=rid, problem="No task delivers this requirement.", fix="Add a task.")
        for rid in report.uncovered_requirements
    ]
    issues += [
        ReviewIssue(
            target=tid,
            problem="This task serves no requirement.",
            fix="Link it to the requirement it serves, or remove it.",
        )
        for tid in report.orphan_tasks
    ]
    issues += [
        ReviewIssue(
            target=rid,
            problem=f"An existing setting covers this, but it's priced over "
            f"{SETTING_MAX_LIKELY_HOURS:g} likely hours.",
            fix="Price it as configuration (0.5 to 1 hour), or explain the extra work.",
        )
        for rid in report.settings_priced_as_custom
    ]
    return issues


def _content(
    transcript: str,
    requirements: list[Requirement],
    fit: list[FitResult],
    questions: list[Question],
    tasks: list[Task],
    report: Coverage,
) -> Content:
    scope = {
        "requirements": [r.model_dump(include=REQUIREMENT_FIELDS) for r in requirements],
        "fit": [f.model_dump(include={"requirement_id", "kind", "reason"}) for f in fit],
        "clarifications": [{"question": q.question, "resolution": q.resolution} for q in questions],
        "tasks": [t.model_dump(include=TASK_FIELDS) for t in tasks],
        "coverage_check": report.model_dump(),
    }
    return [
        # The transcript first, as its own cached block: a second review round reads it again
        cached_text(as_data("transcript", transcript)),
        {"type": "text", "text": "The draft scope and estimate:\n\n" + json.dumps(scope, indent=1)},
    ]


async def review(
    claude: Claude,
    transcript: str,
    requirements: list[Requirement],
    fit: list[FitResult],
    questions: list[Question],
    tasks: list[Task],
    run_tag: str | None = None,
) -> Review:
    report = coverage(requirements, fit, tasks)
    content = _content(transcript, requirements, fit, questions, tasks, report)
    verdict = await claude.call(PROMPT, VERSION, content, Review, run_tag=run_tag)
    found = coverage_issues(report)
    if report.has_gaps or found:
        # Code's findings always count, whatever the model concluded
        return Review(
            verdict="send_back",
            summary=verdict.summary,
            issues=found + verdict.issues,
        )
    return verdict
