"""Follow-up meetings, the change analyst (Sonnet 5.5): scope v2 as a diff against v1.

A second meeting runs steps 1 to 3 again, which gives a fresh requirement list (M1, M2, ...).
The change analyst compares it with the approved scope and labels each item:

- unchanged: the client repeated something already agreed
- changed:   an agreed requirement now asks for something different
- new:       something the approved scope doesn't have
- removed:   the client dropped an agreed requirement

For changed and new items it estimates the extra hours, and says whether the extra work is
inside the signed scope (a clarification) or outside it (a priced change order). Code then
builds v2 from v1: removed work comes out, extra work goes in as new tasks, and every v1 task
and quote stays traceable. v1 itself is never modified.
"""

import json

from pydantic import BaseModel, Field

from app.handoffs import ChangeItem, ChangeLabel, Hours, Requirement, Role, Scope, Task
from app.llm import Claude
from app.text import as_data

PROMPT, VERSION = "analyze_changes", 1


class ProposedChange(BaseModel):
    """What the model returns for one item."""

    label: ChangeLabel
    followup_id: str | None = Field(description="The follow-up meeting's item (M...), if any")
    v1_requirement_id: str | None = Field(description="The approved requirement (R...), if any")
    title: str
    before: str | None = Field(description="What was agreed, for changed or removed items")
    after: str | None = Field(description="What the client wants now, for changed or new items")
    in_signed_scope: bool = Field(
        description="True only when it clarifies agreed work without adding to it"
    )
    extra_low: float = Field(description="Extra hours if all goes well; 0 when none")
    extra_likely: float
    extra_high: float
    role: Role
    reason: str = Field(description="Why this label, citing the meeting")


class ChangeAnalysis(BaseModel):
    changes: list[ProposedChange]


def _content(v1: Scope, meeting: list[Requirement], human_note: str | None) -> str:
    agreed = {
        "requirements": [
            r.model_dump(include={"id", "title", "detail"}) for r in v1.active_requirements()
        ],
        "tasks": [
            t.model_dump(include={"id", "requirement_ids", "title", "likely"}) for t in v1.tasks
        ],
    }
    heard = [
        {**r.model_dump(include={"id", "title", "detail"}), "quotes": [q.text for q in r.quotes]}
        for r in meeting
    ]
    content = (
        f"The approved scope, version {v1.version}:\n\n"
        + as_data("approved_scope", json.dumps(agreed, indent=1))
        + "\n\nWhat the client asked for in the follow-up meeting:\n\n"
        + as_data("followup_requirements", json.dumps(heard, indent=1))
    )
    if human_note:
        content += "\n\nA human reviewer sent your last analysis back with this note:\n\n"
        content += as_data("note", human_note)
    return content


async def analyze(
    claude: Claude,
    v1: Scope,
    meeting: list[Requirement],
    *,
    human_note: str | None = None,
    run_tag: str | None = None,
) -> list[ChangeItem]:
    """One checked ChangeItem per follow-up requirement, plus any removals."""
    if not meeting:
        return []
    content = _content(v1, meeting, human_note)
    reply = await claude.call(PROMPT, VERSION, content, ChangeAnalysis, run_tag=run_tag)
    return check_changes(reply.changes, v1, meeting)


def check_changes(
    proposed: list[ProposedChange], v1: Scope, meeting: list[Requirement]
) -> list[ChangeItem]:
    """Keep only changes that point at real requirements, and never lose a follow-up item."""
    agreed = {r.id for r in v1.active_requirements()}
    heard = {r.id: r for r in meeting}
    seen: set[str] = set()
    changes: list[ChangeItem] = []

    for change in proposed:
        label = change.label
        v1_id = change.v1_requirement_id if change.v1_requirement_id in agreed else None
        m_id = change.followup_id if change.followup_id in heard else None
        if label in ("changed", "removed", "unchanged") and v1_id is None:
            if m_id is None:
                continue  # points at nothing real
            label = "new"  # it claims to change something v1 doesn't have
        if label == "new" and m_id is None:
            continue
        if m_id is not None:
            if m_id in seen:
                continue  # one line per follow-up item
            seen.add(m_id)
        extra = None
        if label in ("changed", "new"):
            low, likely, high = sorted(
                max(0.0, h) for h in (change.extra_low, change.extra_likely, change.extra_high)
            )
            extra = Hours(low=low, likely=likely, high=high)
        changes.append(
            ChangeItem(
                label=label,
                title=change.title,
                v1_requirement_id=v1_id,
                followup_id=m_id,
                before=change.before,
                after=change.after,
                quote=heard[m_id].quotes[0] if m_id else None,
                in_signed_scope=change.in_signed_scope and label != "new",
                extra_hours=extra,
                role=change.role,
                reason=change.reason,
            )
        )

    for m_id, requirement in heard.items():
        if m_id not in seen:  # the model skipped it: show it, for a human to estimate
            changes.append(
                ChangeItem(
                    label="new",
                    title=requirement.title,
                    v1_requirement_id=None,
                    followup_id=m_id,
                    before=None,
                    after=requirement.detail,
                    quote=requirement.quotes[0],
                    in_signed_scope=False,
                    extra_hours=None,
                    reason="The change analyst didn't classify this item. Estimate it by hand.",
                )
            )
    return changes


def build_v2(
    v1: Scope, meeting: list[Requirement], changes: list[ChangeItem], rates: dict[str, float]
) -> Scope:
    """Scope v2: v1 with removals taken out and changed or new work added as new tasks."""
    heard = {r.id: r for r in meeting}
    requirements = [r.model_copy(deep=True) for r in v1.requirements]
    by_id = {r.id: r for r in requirements}
    tasks = [t.model_copy(deep=True) for t in v1.tasks]
    next_req = len(requirements) + 1
    next_task = max((int(t.id[1:]) for t in tasks if t.id[1:].isdigit()), default=0) + 1
    linked: list[ChangeItem] = []

    for change in changes:
        target_id = change.v1_requirement_id
        if change.label == "removed" and target_id:
            by_id[target_id].removed = True
            by_id[target_id].change = "removed"
        elif change.label in ("unchanged", "changed") and target_id:
            requirement = by_id[target_id]
            requirement.change = change.label
            if change.label == "changed":
                requirement.detail = change.after or requirement.detail
            if change.followup_id:
                requirement.quotes += heard[change.followup_id].quotes
        elif change.label == "new" and change.followup_id:
            source = heard[change.followup_id]
            target_id = f"R{next_req}"
            next_req += 1
            requirements.append(
                source.model_copy(update={"id": target_id, "change": "new"}, deep=True)
            )
        linked.append(change.model_copy(update={"v2_requirement_id": target_id}))

        hours = change.extra_hours
        if target_id and hours is not None and hours.high > 0:
            role = change.role
            tasks.append(
                Task(
                    id=f"T{next_task}",
                    requirement_ids=[target_id],
                    title=f"{'New' if change.label == 'new' else 'Change'}: {change.title}",
                    description=change.after or change.title,
                    role=role,
                    low=hours.low,
                    likely=hours.likely,
                    high=hours.high,
                    rate=rates.get(role, rates.get("developer", 0.0)),
                    basis=change.reason,
                    origin="change",
                    change_order=not change.in_signed_scope,
                    ai_hours=hours,
                )
            )
            next_task += 1

    removed = {r.id for r in requirements if r.removed}
    tasks = [t for t in tasks if not t.requirement_ids or not set(t.requirement_ids) <= removed]
    return Scope(
        version=v1.version + 1,
        based_on_version=v1.version,
        requirements=requirements,
        fit=v1.fit,
        questions=v1.questions,
        tasks=tasks,
        assumptions=v1.assumptions,
        exclusions=v1.exclusions,
        changes=linked,
    )
