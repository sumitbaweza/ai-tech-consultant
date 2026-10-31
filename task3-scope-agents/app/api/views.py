"""What the API returns: projects, runs with their steps, and scope versions."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import store
from app.agents import proposal_writer
from app.handoffs import Scope
from app.models import (
    Meeting,
    Project,
    Run,
    RunKind,
    RunStatus,
    ScopeVersion,
    StepStatus,
    VersionStatus,
)


class StepView(BaseModel):
    id: int
    agent: str
    status: StepStatus
    # The agent's handoff object as JSON: requirements, fit results, tasks, a review...
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class VersionSummary(BaseModel):
    id: int
    project_id: int
    run_id: int
    version: int
    status: VersionStatus
    expected_hours: float
    price: float
    change_order_price: float
    decided_by: str | None
    decided_at: datetime | None


class VersionView(VersionSummary):
    scope: Scope
    note: str | None
    # The proposal as Markdown; it says DRAFT at the top until a human approves it
    proposal_markdown: str


class RunSummary(BaseModel):
    id: int
    kind: RunKind
    status: RunStatus
    meeting_id: int
    meeting_title: str
    created_at: datetime


class RunView(RunSummary):
    project_id: int
    error: str | None
    updated_at: datetime
    steps: list[StepView]
    version: VersionSummary | None
    # Dollars spent on Claude by each agent in this run, from the ledger
    costs: dict[str, Decimal]
    total_cost: Decimal


class ProjectView(BaseModel):
    id: int
    name: str
    client: str
    created_at: datetime
    runs: list[RunSummary]
    versions: list[VersionSummary]


def version_summary(version: ScopeVersion) -> VersionSummary:
    totals = Scope.model_validate(version.scope).totals
    return VersionSummary(
        id=version.id,
        project_id=version.project_id,
        run_id=version.run_id,
        version=version.version,
        status=version.status,
        expected_hours=totals.expected_hours,
        price=totals.price,
        change_order_price=totals.change_order_price,
        decided_by=version.decided_by,
        decided_at=version.decided_at,
    )


async def version_view(session: AsyncSession, version: ScopeVersion) -> VersionView:
    project = await store.get(session, Project, version.project_id)
    scope = Scope.model_validate(version.scope)
    approved = version.status != VersionStatus.DRAFT
    return VersionView(
        **version_summary(version).model_dump(),
        scope=scope,
        note=version.note,
        proposal_markdown=proposal_writer.render(scope, project.client, approved=approved),
    )


async def run_view(session: AsyncSession, run: Run) -> RunView:
    meeting = await store.get(session, Meeting, run.meeting_id)
    version = await store.run_version(session, run.id)
    costs = await store.run_costs(session, run.id)
    return RunView(
        id=run.id,
        kind=run.kind,
        status=run.status,
        meeting_id=meeting.id,
        meeting_title=meeting.title,
        created_at=run.created_at,
        project_id=run.project_id,
        error=run.error,
        updated_at=run.updated_at,
        steps=[
            StepView.model_validate(step, from_attributes=True)
            for step in await store.steps(session, run.id)
        ],
        version=version_summary(version) if version else None,
        costs=costs,
        total_cost=sum(costs.values(), Decimal(0)),
    )


async def project_view(session: AsyncSession, project: Project) -> ProjectView:
    rows = await session.execute(
        select(Run, Meeting.title)
        .join(Meeting, Meeting.id == Run.meeting_id)
        .where(Run.project_id == project.id)
        .order_by(Run.id)
    )
    versions = await session.scalars(
        select(ScopeVersion)
        .where(ScopeVersion.project_id == project.id)
        .order_by(ScopeVersion.version)
    )
    return ProjectView(
        id=project.id,
        name=project.name,
        client=project.client,
        created_at=project.created_at,
        runs=[
            RunSummary(
                id=run.id,
                kind=run.kind,
                status=run.status,
                meeting_id=run.meeting_id,
                meeting_title=title,
                created_at=run.created_at,
            )
            for run, title in rows
        ],
        versions=[version_summary(v) for v in versions],
    )
