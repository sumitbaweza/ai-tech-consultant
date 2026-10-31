"""After approval, the learner (Sonnet 5.5): estimation rules from how a human edited hours.

When a human changes the estimator's hours before approving ("this header task is 2 hours,
not 1"), the next estimate should know. The learner reads the edits of one approved scope and
the current playbook, and returns the updated rule list: existing rules kept, revised or
dropped, and at most three new ones. Code saves it as a new playbook version; the estimator
reads the active version, and an older version can be made active again (rollback).

No edits, no call: an approval where the human changed nothing teaches nothing and costs
nothing.
"""

import json
from typing import Literal

from pydantic import BaseModel

from app.handoffs import EstimationRule, Hours, Playbook, Role, Scope
from app.llm import Claude
from app.text import as_data

PROMPT, VERSION = "learn_estimation_rules", 1
MAX_NEW_RULES = 3


class HourEdit(BaseModel):
    """One task a human changed, added or removed before approving."""

    kind: Literal["changed", "added", "removed"]
    task: str
    role: Role | None
    ai: Hours | None  # the estimator's hours; None for a task the human added
    approved: Hours | None  # the hours approved; None for a task the human removed


class Rules(BaseModel):
    rules: list[EstimationRule]


def hour_edits(scope: Scope, removed_titles: list[str]) -> list[HourEdit]:
    """What the human changed in the estimate, read from the approved scope: the estimator's
    hours are kept next to the approved ones in every task (`ai_hours`)."""
    edits: list[HourEdit] = []
    for task in scope.tasks:
        approved = Hours(low=task.low, likely=task.likely, high=task.high)
        if task.origin == "human":
            edits.append(
                HourEdit(kind="added", task=task.title, role=task.role, ai=None, approved=approved)
            )
        elif task.ai_hours is not None and task.ai_hours != approved:
            edits.append(
                HourEdit(
                    kind="changed",
                    task=task.title,
                    role=task.role,
                    ai=task.ai_hours,
                    approved=approved,
                )
            )
    edits += [
        HourEdit(kind="removed", task=title, role=None, ai=None, approved=None)
        for title in removed_titles
    ]
    return edits


def _content(current: Playbook | None, edits: list[HourEdit]) -> str:
    rules = [rule.model_dump() for rule in current.rules] if current else []
    return (
        "The current estimation playbook:\n\n"
        + as_data("playbook", json.dumps(rules, indent=1))
        + "\n\nHow a human changed the AI's estimate before approving it:\n\n"
        + as_data("edits", json.dumps([e.model_dump(mode="json") for e in edits], indent=1))
    )


async def learn(
    claude: Claude,
    current: Playbook | None,
    scope: Scope,
    removed_titles: list[str],
    run_tag: str | None = None,
) -> list[EstimationRule] | None:
    """The updated rules, or None when there is nothing to learn (no edits, or no change)."""
    edits = hour_edits(scope, removed_titles)
    if not edits:
        return None
    reply = await claude.call(PROMPT, VERSION, _content(current, edits), Rules, run_tag=run_tag)
    before = current.rules if current else []
    rules = reply.rules[: len(before) + MAX_NEW_RULES]
    return None if rules == before else rules
