"""Projects, and the meetings that start agent runs."""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import store
from app.api.deps import OrchestratorDep, SessionDep, launch, require_claude
from app.api.views import ProjectView, RunView, project_view, run_view
from app.models import Project, RunStatus

router = APIRouter(tags=["projects"])


class NewProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client: str = Field(min_length=1, max_length=200)


class NewMeeting(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # About 500,000 tokens: far longer than any meeting, short enough to refuse a stray file
    transcript: str = Field(min_length=1, max_length=2_000_000)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: NewProject, session: SessionDep) -> ProjectView:
    project = Project(name=body.name, client=body.client)
    session.add(project)
    await session.commit()
    return await project_view(session, project)


@router.get("/projects")
async def list_projects(session: SessionDep) -> list[ProjectView]:
    projects = await session.scalars(select(Project).order_by(Project.id.desc()))
    return [await project_view(session, project) for project in projects]


@router.get("/projects/{project_id}")
async def get_project(project_id: int, session: SessionDep) -> ProjectView:
    return await project_view(session, await store.get(session, Project, project_id))


@router.post(
    "/projects/{project_id}/meetings",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_claude)],
)
async def add_meeting(
    project_id: int, body: NewMeeting, session: SessionDep, orchestrator: OrchestratorDep
) -> RunView:
    """Save a transcript and start the agent team on it in the background.

    A project's first meeting runs all 8 steps and ends in a draft scope v1. Once a version is
    approved, the next meeting is a follow-up: steps 1 to 3, then the change analyst, ending
    in a draft of the next version. Poll GET /v1/runs/{id} to follow the steps. 409 when the
    project already has an open run.
    """
    _, run = await store.add_meeting(session, project_id, body.title, body.transcript)
    run = await store.claim_run(session, run.id, RunStatus.QUEUED)
    launch(orchestrator.start(run.id))
    return await run_view(session, run)
