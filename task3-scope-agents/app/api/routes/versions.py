"""Scope versions and the human gate: edit, approve, send back, and export once approved."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import store
from app.agents import human_approval, proposal_writer
from app.api.deps import OrchestratorDep, SessionDep, SettingsDep, launch, require_claude
from app.api.views import RunView, VersionView, run_view, version_view
from app.handoffs import Scope
from app.models import Project, Run, RunStatus, ScopeVersion, Step, StepStatus, VersionStatus
from app.orchestrator import Decision

router = APIRouter(tags=["versions"])


class EditScope(human_approval.ScopeEdit):
    by: str = Field(min_length=1, max_length=200)


class Approval(BaseModel):
    by: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=5000)


class SendBack(BaseModel):
    by: str = Field(min_length=1, max_length=200)
    # What the agents should fix: it goes to the estimator (or the change analyst) as is
    note: str = Field(min_length=1, max_length=5000)


def _record(run_id: int, agent: str, output: BaseModel) -> Step:
    """A human action, saved as a step next to the agents' steps."""
    return Step(
        run_id=run_id,
        agent=agent,
        status=StepStatus.DONE,
        output=output.model_dump(mode="json"),
        finished_at=datetime.now(UTC),
    )


async def _draft(session: SessionDep, version_id: int) -> ScopeVersion:
    """A version a human can still change: a draft whose run waits for approval."""
    version = await store.get(session, ScopeVersion, version_id)
    run = await store.get(session, Run, version.run_id)
    if version.status != VersionStatus.DRAFT or run.status != RunStatus.AWAITING_APPROVAL:
        raise store.Conflict(
            f"Version {version.version} is {version.status} and its run is {run.status}: "
            "only a draft waiting for approval can change"
        )
    return version


@router.get("/versions/{version_id}")
async def get_version(version_id: int, session: SessionDep) -> VersionView:
    """The scope, its totals and the proposal preview (marked DRAFT until approved)."""
    return await version_view(session, await store.get(session, ScopeVersion, version_id))


@router.put("/versions/{version_id}/scope")
async def edit_scope(
    version_id: int, body: EditScope, session: SessionDep, settings: SettingsDep
) -> VersionView:
    """Replace the estimate table, assumptions and exclusions with a human's edits. Each
    changed field is recorded, next to the estimator's own hours."""
    version = await _draft(session, version_id)
    scope = Scope.model_validate(version.scope)
    try:
        edited, record = human_approval.apply_edit(scope, body, settings.scope_rates)
    except human_approval.EditError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    version.scope = edited.model_dump(mode="json")
    session.add(_record(version.run_id, "human_edit", record))
    await session.commit()
    return await version_view(session, version)


@router.post("/versions/{version_id}/approve")
async def approve(
    version_id: int, body: Approval, session: SessionDep, orchestrator: OrchestratorDep
) -> VersionView:
    """Approve the draft: from now on the proposal can go to the client. The project's
    previous approved version is marked superseded and kept."""
    version = await _draft(session, version_id)
    session.add(
        _record(
            version.run_id,
            "human_approval",
            Decision(by=body.by, note=body.note, version=version.version),
        )
    )
    version = await store.approve(session, version, body.by, body.note)
    # The human's hour edits become estimation rules, in the background
    launch(orchestrator.learn(version.id))
    return await version_view(session, version)


@router.post(
    "/versions/{version_id}/send-back",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_claude)],
)
async def send_back(
    version_id: int, body: SendBack, session: SessionDep, orchestrator: OrchestratorDep
) -> RunView:
    """Send the draft back to the agents with a note. A first meeting's estimate is redone
    from the draft as it stands, edits included; a follow-up's change analysis is redone."""
    version = await _draft(session, version_id)
    session.add(
        _record(
            version.run_id,
            "human_send_back",
            Decision(by=body.by, note=body.note, version=version.version),
        )
    )
    await session.commit()
    run = await store.claim_run(session, version.run_id, RunStatus.AWAITING_APPROVAL)
    launch(orchestrator.redo(run.id, human_note=body.note))
    return await run_view(session, run)


@router.get("/versions/{version_id}/proposal.md", response_class=PlainTextResponse)
async def export_proposal(version_id: int, session: SessionDep) -> str:
    """The client-ready proposal, as Markdown. Refused (409) until a human approves the
    version: this is the only way a proposal leaves the system."""
    version = await store.get(session, ScopeVersion, version_id)
    if version.status == VersionStatus.DRAFT:
        raise store.Conflict("Not approved yet: a human must approve this version first")
    project = await store.get(session, Project, version.project_id)
    scope = Scope.model_validate(version.scope)
    return proposal_writer.render(scope, project.client, approved=True)
