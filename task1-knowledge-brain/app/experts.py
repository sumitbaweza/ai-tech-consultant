"""The expert queue: questions the help center didn't answer, or answered wrongly, for a human.

POST /v1/ask opens a task when the help center doesn't cover a question, and a client flags a
wrong answer (a thumbs down) with POST /v1/expert-tasks. A question asked again doesn't open a
second task: while its task is open, or after it was dismissed, each new ask adds 1 to that
task's count, so the queue can show the most asked questions first.

The expert's answer becomes knowledge. It's saved as a page of kind `expert`, titled with the
question, which the worker embeds like an article, so search and /v1/ask use it from then on,
and cite it as an expert answer. Archiving that page takes the answer back out.
"""

import hashlib
import re
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExpertReason,
    ExpertStatus,
    ExpertTask,
    Page,
    PageKind,
    PageStatus,
    PageVersion,
)
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

# The category of every expert answer page, so its chunks start "Expert answers > <question>"
EXPERT_CATEGORY = "Expert answers"


class TaskClosed(Exception):
    """The task was already answered or dismissed."""


class NoAnswer(Exception):
    """The task has no answer yet."""


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


async def answer_task(session: AsyncSession, task_id: int, answer: str, expert: str) -> ExpertTask:
    """Save the expert's answer as knowledge, on the question's expert page. A dismissed task
    can still be answered, and answering again replaces the answer: the page's change history
    keeps each one. Raises LookupError for an unknown task."""
    # Locked until the commit, so an answer and a dismissal can't both close it
    task = await session.get(ExpertTask, task_id, with_for_update=True)
    if task is None:
        raise LookupError(f"No expert task with id {task_id}")

    page = await _answer_page(session, task)
    # The hash covers the question too, since the worker embeds both
    content_hash = hashlib.sha256(f"{page.title}\n\n{answer}".encode()).hexdigest()
    if content_hash != page.content_hash:  # a new answer, which the worker embeds again
        page.content_md, page.content_hash = answer, content_hash
        session.add(
            PageVersion(
                page_id=page.id, content_hash=content_hash, title=page.title, content_md=answer
            )
        )
    page.status = PageStatus.ACTIVE  # back in search, if the answer had been archived

    task.status = ExpertStatus.ANSWERED
    task.answer, task.answered_by, task.answered_at = answer, expert, datetime.now(UTC)
    task.answer_page_id = page.id
    await session.commit()
    return task


async def archive_answer(session: AsyncSession, task_id: int) -> ExpertTask:
    """Take the task's answer out of search and answers, as when it turned out wrong. Its page
    is archived, kept with its history, never deleted; answering again brings it back. Raises
    LookupError for an unknown task, and NoAnswer for a task without an answer."""
    task = await session.get(ExpertTask, task_id)
    if task is None:
        raise LookupError(f"No expert task with id {task_id}")
    if task.answer_page_id is None:
        raise NoAnswer(f"Expert task {task_id} has no answer to archive")
    page = await session.get_one(Page, task.answer_page_id, with_for_update=True)
    page.status, page.archived_at = PageStatus.ARCHIVED, datetime.now(UTC)
    await session.commit()
    return task


async def _answer_page(session: AsyncSession, task: ExpertTask) -> Page:
    """The page for this question's expert answer. Tasks for the same question share one, so a
    question asked again after its answer, and answered again, updates the same page."""
    page = await session.scalar(
        select(Page)
        .join(ExpertTask, ExpertTask.answer_page_id == Page.id)
        .where(ExpertTask.question_key == task.question_key)
        .order_by(Page.id)
        .limit(1)
    )
    if page is None:
        page = Page(
            url=f"expert-answer:{task.id}",  # not a web page: unique, and says what it is
            kind=PageKind.EXPERT,
            status=PageStatus.ACTIVE,
            title=task.question,
            category=EXPERT_CATEGORY,
        )
        session.add(page)
        await session.flush()  # gives the page its id
    return page
