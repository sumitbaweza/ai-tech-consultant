"""Reads and writes for projects, runs, steps and scope versions.

The orchestrator and the API share these, so the rules about what may happen when (one open
run per project, follow-ups only after an approval, approved versions frozen) live in one place.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.handoffs import EstimationRule, Playbook, Scope
from app.models import (
    Base,
    LLMCall,
    Meeting,
    PlaybookVersion,
    Project,
    Run,
    RunKind,
    RunStatus,
    ScopeVersion,
    Step,
    StepStatus,
    VersionStatus,
)

# A run in one of these states blocks a new meeting for its project
OPEN_STATUSES = (
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.AWAITING_ANSWERS,
    RunStatus.AWAITING_APPROVAL,
)


class Conflict(Exception):
    """The request clashes with the project's state; the message says what to do instead."""


class NotFound(LookupError):
    pass


def run_tag(run_id: int, agent: str) -> str:
    """The ledger tag that ties a Claude call to its run and agent."""
    return f"run-{run_id}/{agent}"


async def get[T: Base](session: AsyncSession, model: type[T], row_id: int) -> T:
    row = await session.get(model, row_id)
    if row is None:
        raise NotFound(f"{model.__name__} {row_id} doesn't exist")
    return row


# --- Meetings and runs --------------------------------------------------------------------


async def add_meeting(
    session: AsyncSession, project_id: int, title: str, transcript: str
) -> tuple[Meeting, Run]:
    """Save a transcript and queue a run for it. A project with an approved scope gets a
    follow-up run, compared against that scope; otherwise a first-meeting run."""
    await get(session, Project, project_id)
    open_run = await session.scalar(
        select(Run.id).where(Run.project_id == project_id, Run.status.in_(OPEN_STATUSES))
    )
    if open_run is not None:
        raise Conflict(f"Run {open_run} for this project is still open: finish it first")
    approved = await session.scalar(
        select(ScopeVersion)
        .where(
            ScopeVersion.project_id == project_id,
            ScopeVersion.status == VersionStatus.APPROVED,
        )
        .order_by(ScopeVersion.version.desc())
    )
    meeting = Meeting(project_id=project_id, title=title, transcript=transcript)
    session.add(meeting)
    await session.flush()  # gives the meeting its id
    run = Run(
        project_id=project_id,
        meeting_id=meeting.id,
        kind=RunKind.FOLLOWUP if approved else RunKind.FIRST,
        status=RunStatus.QUEUED,
        base_version_id=approved.id if approved else None,
    )
    session.add(run)
    await session.commit()
    return meeting, run


async def claim_run(session: AsyncSession, run_id: int, expected: RunStatus) -> Run:
    """Move a run from `expected` to RUNNING, or raise Conflict if it isn't in that state.
    One UPDATE with the old status in its WHERE, so two clicks can't start two pipelines."""
    result = await session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == expected)
        .values(status=RunStatus.RUNNING, error=None)
        .returning(Run.id)
    )
    await session.commit()
    run = await session.get(Run, run_id, populate_existing=True)  # reload what UPDATE changed
    if run is None:
        raise NotFound(f"Run {run_id} doesn't exist")
    if result.scalar() is None:
        raise Conflict(f"Run {run_id} is {run.status}, not {expected}")
    return run


async def set_status(
    session: AsyncSession, run_id: int, status: RunStatus, error: str | None = None
) -> None:
    await session.execute(update(Run).where(Run.id == run_id).values(status=status, error=error))
    await session.commit()


async def fail_interrupted_runs(session: AsyncSession) -> int:
    """At startup: runs left RUNNING or QUEUED by a restart can't still be running."""
    result = await session.execute(
        update(Run)
        .where(Run.status.in_((RunStatus.RUNNING, RunStatus.QUEUED)))
        .values(status=RunStatus.FAILED, error="Interrupted by a restart. Press Retry.")
        .returning(Run.id)
    )
    await session.commit()
    return len(result.all())


# --- Steps --------------------------------------------------------------------------------


async def start_step(session: AsyncSession, run_id: int, agent: str) -> int:
    step = Step(run_id=run_id, agent=agent, status=StepStatus.RUNNING)
    session.add(step)
    await session.commit()
    return step.id


async def finish_step(
    session: AsyncSession, step_id: int, output: BaseModel | None = None, error: str | None = None
) -> None:
    values: dict[str, Any] = {
        "status": StepStatus.FAILED if error else StepStatus.DONE,
        "output": output.model_dump(mode="json") if output is not None else None,
        "error": error,
        "finished_at": datetime.now(UTC),
    }
    await session.execute(update(Step).where(Step.id == step_id).values(**values))
    await session.commit()


async def latest_output[T: BaseModel](
    session: AsyncSession, run_id: int, agent: str, output: type[T]
) -> T | None:
    """The newest finished output of `agent` in this run, read back as its handoff object."""
    data = await session.scalar(
        select(Step.output)
        .where(Step.run_id == run_id, Step.agent == agent, Step.status == StepStatus.DONE)
        .order_by(Step.id.desc())
        .limit(1)
    )
    return output.model_validate(data) if data is not None else None


async def steps(session: AsyncSession, run_id: int) -> list[Step]:
    return list(await session.scalars(select(Step).where(Step.run_id == run_id).order_by(Step.id)))


# --- Scope versions -----------------------------------------------------------------------


async def run_version(session: AsyncSession, run_id: int) -> ScopeVersion | None:
    return await session.scalar(select(ScopeVersion).where(ScopeVersion.run_id == run_id))


async def save_draft(session: AsyncSession, run: Run, scope: Scope) -> ScopeVersion:
    """Create the run's draft version, or replace its scope after a send-back."""
    version = await run_version(session, run.id)
    if version is None:
        version = ScopeVersion(
            project_id=run.project_id,
            run_id=run.id,
            version=scope.version,
            status=VersionStatus.DRAFT,
            scope=scope.model_dump(mode="json"),
        )
        session.add(version)
    elif version.status != VersionStatus.DRAFT:
        raise Conflict(f"Version {version.version} is {version.status} and can't change")
    else:
        version.scope = scope.model_dump(mode="json")
    await session.commit()
    return version


async def next_version_number(session: AsyncSession, project_id: int) -> int:
    highest = await session.scalar(
        select(func.max(ScopeVersion.version)).where(ScopeVersion.project_id == project_id)
    )
    return (highest or 0) + 1


async def approve(
    session: AsyncSession, version: ScopeVersion, by: str, note: str | None
) -> ScopeVersion:
    """Approve a draft. The project's earlier approved version becomes superseded, but stays."""
    if version.status != VersionStatus.DRAFT:
        raise Conflict(f"Version {version.version} is {version.status}, not a draft")
    await session.execute(
        update(ScopeVersion)
        .where(
            ScopeVersion.project_id == version.project_id,
            ScopeVersion.status == VersionStatus.APPROVED,
        )
        .values(status=VersionStatus.SUPERSEDED)
    )
    version.status = VersionStatus.APPROVED
    version.decided_by = by
    version.decided_at = datetime.now(UTC)
    version.note = note
    await session.execute(
        update(Run).where(Run.id == version.run_id).values(status=RunStatus.APPROVED)
    )
    await session.commit()
    return version


# --- The estimation playbook -------------------------------------------------------------


async def active_playbook(session: AsyncSession) -> Playbook | None:
    """The rules the estimator follows now, or None before anything was learned."""
    row = await session.scalar(select(PlaybookVersion).where(PlaybookVersion.is_active))
    return Playbook.model_validate({"version": row.version, **row.rules}) if row else None


async def save_playbook(
    session: AsyncSession, rules: list[EstimationRule], learned_from_id: int | None
) -> Playbook:
    """Save rules as the next playbook version and make it the active one."""
    highest = await session.scalar(select(func.max(PlaybookVersion.version)))
    playbook = Playbook(version=(highest or 0) + 1, rules=rules)
    await session.execute(update(PlaybookVersion).values(is_active=False))
    session.add(
        PlaybookVersion(
            version=playbook.version,
            rules={"rules": [rule.model_dump(mode="json") for rule in rules]},
            learned_from_id=learned_from_id,
            is_active=True,
        )
    )
    await session.commit()
    return playbook


async def activate_playbook(session: AsyncSession, version: int) -> None:
    """Rollback (or roll forward): make an existing version the active one."""
    exists = await session.scalar(
        select(PlaybookVersion.id).where(PlaybookVersion.version == version)
    )
    if exists is None:
        raise NotFound(f"Playbook version {version} doesn't exist")
    await session.execute(
        update(PlaybookVersion).values(is_active=PlaybookVersion.version == version)
    )
    await session.commit()


# --- Cost ---------------------------------------------------------------------------------


async def run_costs(session: AsyncSession, run_id: int) -> dict[str, Decimal]:
    """Dollars spent on Claude by each agent of a run, from the ledger."""
    rows = await session.execute(
        select(LLMCall.run_tag, func.sum(LLMCall.cost_usd))
        .where(LLMCall.run_tag.like(f"run-{run_id}/%"))
        .group_by(LLMCall.run_tag)
    )
    return {tag.split("/", 1)[1]: total for tag, total in rows if tag is not None}
