"""The expert queue: questions the help center doesn't answer, waiting for a human expert."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import SessionDep
from app.experts import TaskClosed, dismiss_task, list_tasks, open_task
from app.models import ExpertReason, ExpertStatus
from app.rag.answer import ChatTurn, Source

router = APIRouter(prefix="/expert-tasks", tags=["expert tasks"])


class ExpertTaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    history: list[ChatTurn]  # the chat before the question
    reason: ExpertReason
    status: ExpertStatus
    asked_count: int  # how often the question was asked or flagged
    created_at: datetime
    last_asked_at: datetime
    given_answer: str | None  # for a thumbs down: the answer that was wrong
    retrieved: list[Source]  # what search found when the question was first asked
    answer: str | None
    answered_by: str | None  # who answered or dismissed it
    answered_at: datetime | None


class ExpertTaskList(BaseModel):
    total: int  # every task with that status; `items` is one page of them
    items: list[ExpertTaskView]


class FlagAnswer(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
        description="The question. Best: the standalone_question from the answer you got.",
    )
    history: list[ChatTurn] = Field(default_factory=list[ChatTurn], max_length=20)
    answer: str = Field(min_length=1, max_length=10_000, description="The answer that was wrong")


class Dismiss(BaseModel):
    expert: str = Field(min_length=1, max_length=200, description="Who dismissed it")


@router.get("")
async def expert_queue(
    session: SessionDep,
    task_status: Annotated[ExpertStatus, Query(alias="status")] = ExpertStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExpertTaskList:
    """The queue: open tasks by default, the most asked first. Each shows the chat it came
    from, why the Brain didn't answer, and what search found."""
    total, tasks = await list_tasks(session, task_status, limit, offset)
    return ExpertTaskList(total=total, items=[ExpertTaskView.model_validate(t) for t in tasks])


@router.post("", status_code=status.HTTP_201_CREATED)
async def flag_wrong_answer(body: FlagAnswer, session: SessionDep) -> ExpertTaskView:
    """A thumbs down: the Brain's answer was wrong. The question goes to the expert queue with
    the answer it got. If the question is already in the queue, it counts one more time."""
    task = await open_task(
        session,
        question=body.question,
        history=body.history,
        reason=ExpertReason.THUMBS_DOWN,
        sources=[],
        given_answer=body.answer,
    )
    await session.commit()
    return ExpertTaskView.model_validate(task)


@router.post("/{task_id}/dismiss")
async def dismiss(task_id: int, body: Dismiss, session: SessionDep) -> ExpertTaskView:
    """Close a task without an answer, such as a question off topic for the help center. The
    same question asked later counts on this task, so it doesn't come back to the queue."""
    try:
        task = await dismiss_task(session, task_id, body.expert)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except TaskClosed as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return ExpertTaskView.model_validate(task)
