"""The last step, human approval: a person edits, approves or sends back. No model runs here.

Nothing reaches the client before a person approves the scope. The rules live here so the API
and the tests share them:

- A draft can be edited (hours, tasks, assumptions, exclusions) and approved or sent back.
- An approved version is frozen; a follow-up meeting makes the next version instead.
- The proposal can only be exported once its version is approved (see app.api.routes).

Every edit is recorded as before and after, next to the estimator's own hours (`ai_hours`),
so the gap between AI hours and approved hours can be measured over time.
"""

from pydantic import BaseModel, Field

from app.handoffs import Role, Scope, Task


class TaskEdit(BaseModel):
    """One row of the estimate table as the human left it. No id means a new task."""

    id: str | None = None
    requirement_ids: list[str]
    title: str = Field(min_length=1)
    description: str = ""
    role: Role
    low: float = Field(ge=0)
    likely: float = Field(ge=0)
    high: float = Field(ge=0)
    change_order: bool = False


class ScopeEdit(BaseModel):
    tasks: list[TaskEdit]
    assumptions: list[str]
    exclusions: list[str]


class FieldChange(BaseModel):
    task_id: str
    field: str
    before: str | None
    after: str | None


class EditRecord(BaseModel):
    """What a human changed, saved as a step so the learning loop can read it later."""

    changes: list[FieldChange]
    ai_expected_hours: float  # the estimator's total, before any human touched it
    expected_hours_before: float
    expected_hours_after: float


class EditError(ValueError):
    """The edit can't be applied, e.g. low > likely."""


TRACKED_FIELDS = ("title", "role", "low", "likely", "high", "change_order", "requirement_ids")


def apply_edit(scope: Scope, edit: ScopeEdit, rates: dict[str, float]) -> tuple[Scope, EditRecord]:
    """The scope with the human's table applied, and a record of what changed."""
    known = {r.id for r in scope.requirements}
    old = {task.id: task for task in scope.tasks}
    next_number = max((int(t.id[1:]) for t in scope.tasks if t.id[1:].isdigit()), default=0) + 1
    tasks: list[Task] = []
    changes: list[FieldChange] = []

    for row in edit.tasks:
        if not row.low <= row.likely <= row.high:
            raise EditError(f"'{row.title}': hours must be low <= likely <= high")
        unknown = set(row.requirement_ids) - known
        if unknown:
            raise EditError(f"'{row.title}': unknown requirement ids {sorted(unknown)}")
        before = old.get(row.id or "")
        if before is None:
            task = Task(
                id=f"T{next_number}",
                requirement_ids=row.requirement_ids,
                title=row.title,
                description=row.description,
                role=row.role,
                low=row.low,
                likely=row.likely,
                high=row.high,
                rate=rates.get(row.role, rates.get("developer", 0.0)),
                basis="Added by a human reviewer.",
                origin="human",
                change_order=row.change_order,
            )
            next_number += 1
            changes.append(
                FieldChange(task_id=task.id, field="added", before=None, after=task.title)
            )
        else:
            task = before.model_copy(
                update={
                    **row.model_dump(exclude={"id"}),
                    # A new role takes that role's rate; otherwise the task keeps its rate
                    "rate": before.rate
                    if row.role == before.role
                    else rates.get(row.role, before.rate),
                }
            )
            for field in TRACKED_FIELDS:
                was, now = getattr(before, field), getattr(task, field)
                if was != now:
                    changes.append(
                        FieldChange(task_id=task.id, field=field, before=str(was), after=str(now))
                    )
        tasks.append(task)

    kept = {task.id for task in tasks}
    changes += [
        FieldChange(task_id=task_id, field="removed", before=task.title, after=None)
        for task_id, task in old.items()
        if task_id not in kept
    ]
    edited = scope.model_copy(
        update={"tasks": tasks, "assumptions": edit.assumptions, "exclusions": edit.exclusions}
    )
    record = EditRecord(
        changes=changes,
        ai_expected_hours=round(
            sum(t.ai_hours.expected for t in scope.tasks if t.ai_hours is not None), 1
        ),
        expected_hours_before=scope.totals.expected_hours,
        expected_hours_after=edited.totals.expected_hours,
    )
    return edited, record
