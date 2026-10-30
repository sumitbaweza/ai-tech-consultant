"""The expert queue: questions the help center didn't answer, or answered wrongly, for a human.

POST /v1/ask opens a task when the help center doesn't cover a question, and a client flags a
wrong answer (a thumbs down) with POST /v1/expert-tasks. A question asked again doesn't open a
second task: while its task is open, or after it was dismissed, each new ask adds 1 to that
task's count, so the queue can show the most asked questions first.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExpertReason, ExpertStatus, ExpertTask
from app.rag.answer import ChatTurn, Outcome, Source

# The /v1/ask outcomes a human expert should see. A model failure isn't one: the help center
# may well cover the question, and asking again later can work.
EXPERT_REASONS = {
    Outcome.LOW_RETRIEVAL: ExpertReason.LOW_RETRIEVAL,
    Outcome.NOT_IN_SOURCES: ExpertReason.NOT_IN_SOURCES,
    Outcome.NO_VALID_CITATION: ExpertReason.NO_VALID_CITATION,
}

# The tasks a repeated question counts on. This must match the partial unique index on
# question_key (app/models.py), so that Postgres uses the index to spot the repeat.
OPEN_OR_DISMISSED = text("status IN ('open', 'dismissed')")


class TaskClosed(Exception):
    """The task was already answered or dismissed."""


def question_key(question: str) -> str:
    """The question's words in lower case: "How do I add a logo?" and "how do I add a LOGO"
    are the same question."""
    return " ".join(re.findall(r"\w+", question.lower()))


async def open_task(
    session: AsyncSession,
    *,
    question: str,
    history: list[ChatTurn],
    reason: ExpertReason,
    sources: list[Source],
    given_answer: str | None = None,
) -> ExpertTask:
    """Open a task for the question, or count one more ask on its open or dismissed task. One
    INSERT ... ON CONFLICT does either, so two asks arriving at once can't open two tasks. The
    caller commits."""
    upsert = insert(ExpertTask).values(
        question=question,
        question_key=question_key(question),
        history=[turn.model_dump() for turn in history],
        retrieved=[source.model_dump() for source in sources],
        reason=reason,
        status=ExpertStatus.OPEN,
        given_answer=given_answer,
    )
    upsert = upsert.on_conflict_do_update(
        index_elements=[ExpertTask.question_key],
        index_where=OPEN_OR_DISMISSED,
        set_={
            "asked_count": ExpertTask.asked_count + 1,
            "last_asked_at": func.now(),
            # A thumbs down on a question already in the queue adds the answer it got
            "given_answer": func.coalesce(upsert.excluded.given_answer, ExpertTask.given_answer),
        },
    )
    tasks = await session.scalars(
        upsert.returning(ExpertTask), execution_options={"populate_existing": True}
    )
    return tasks.one()


async def list_tasks(
    session: AsyncSession, status: ExpertStatus, limit: int, offset: int
) -> tuple[int, list[ExpertTask]]:
    """How many tasks have this status, and one page of them: the most asked first, then the
    most recently asked."""
    with_status = ExpertTask.status == status
    total = await session.scalar(select(func.count()).select_from(ExpertTask).where(with_status))
    tasks = await session.scalars(
        select(ExpertTask)
        .where(with_status)
        .order_by(ExpertTask.asked_count.desc(), ExpertTask.last_asked_at.desc(), ExpertTask.id)
        .limit(limit)
        .offset(offset)
    )
    return total or 0, list(tasks)


async def dismiss_task(session: AsyncSession, task_id: int, expert: str) -> ExpertTask:
    """Close a task without an answer, such as a question off topic for the help center.
    Raises LookupError for an unknown task, and TaskClosed for one already closed."""
    # Locked until the commit, so an answer and a dismissal can't both close it
    task = await session.get(ExpertTask, task_id, with_for_update=True)
    if task is None:
        raise LookupError(f"No expert task with id {task_id}")
    if task.status != ExpertStatus.OPEN:
        raise TaskClosed(f"Expert task {task_id} is already {task.status}")
    task.status = ExpertStatus.DISMISSED
    task.answered_by, task.answered_at = expert, datetime.now(UTC)
    await session.commit()
    return task
