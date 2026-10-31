"""Runs: follow the agents' steps, answer the consultant's questions, retry a failed run."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app import store
from app.api.deps import OrchestratorDep, SessionDep, launch, require_claude
from app.api.views import RunView, run_view
from app.models import Run, RunStatus, Step, StepStatus
from app.orchestrator import Questions

router = APIRouter(tags=["runs"])


class AnswerIn(BaseModel):
    question_id: str
    # Empty or missing: the question's default assumption goes into the proposal instead
    answer: str | None = Field(default=None, max_length=5000)


class Answers(BaseModel):
    by: str = Field(min_length=1, max_length=200)
    answers: list[AnswerIn]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: SessionDep) -> RunView:
    """The run's status and every step so far, each with its agent's output."""
    return await run_view(session, await store.get(session, Run, run_id))


@router.post(
    "/runs/{run_id}/answers",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_claude)],
)
async def answer_questions(
    run_id: int, body: Answers, session: SessionDep, orchestrator: OrchestratorDep
) -> RunView:
    """Answer the consultant's questions (any may be left blank), then the run goes on to
    the estimate. Blank answers become written assumptions in the proposal."""
    asked = await store.latest_output(session, run_id, "consultant", Questions)
    run = await store.claim_run(session, run_id, RunStatus.AWAITING_ANSWERS)
    given = {a.question_id: (a.answer or "").strip() or None for a in body.answers}
    answered = Questions(
        questions=[
            q.model_copy(update={"answer": given.get(q.id)})
            for q in (asked.questions if asked else [])
        ]
    )
    session.add(
        Step(
            run_id=run_id,
            agent="human_answers",
            status=StepStatus.DONE,
            output={"by": body.by, **answered.model_dump(mode="json")},
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()
    launch(orchestrator.resume_with_answers(run.id))
    return await run_view(session, run)


@router.post(
    "/runs/{run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_claude)],
)
async def retry_run(run_id: int, session: SessionDep, orchestrator: OrchestratorDep) -> RunView:
    """Start a failed run's current phase again. Finished steps before it are reused."""
    run = await store.claim_run(session, run_id, RunStatus.FAILED)
    launch(orchestrator.redo(run.id, human_note=None))
    return await run_view(session, run)
