"""Expert queue: the questions the help center doesn't answer, waiting for a human expert. An
answer becomes knowledge that the Brain cites from then on; a dismissed question stays out of
the queue."""

import streamlit as st

from app.api.routes.expert_tasks import ExpertTaskView
from app.models import ExpertReason, ExpertStatus
from ui import api
from ui.common import flash, show_flash, signed_in_user, when

REASONS = {
    ExpertReason.LOW_RETRIEVAL: "Nothing in the help center came close",
    ExpertReason.NOT_IN_SOURCES: "The closest articles don't answer it",
    ExpertReason.NO_VALID_CITATION: "The answer cited none of its sources",
    ExpertReason.THUMBS_DOWN: "Someone marked the Brain's answer as wrong",
}


def render() -> None:
    st.title("Expert queue")
    show_flash()
    waiting, answered, dismissed = st.tabs(["Waiting", "Answered", "Dismissed"])
    with waiting:
        task_list(ExpertStatus.OPEN)
    with answered:
        task_list(ExpertStatus.ANSWERED)
    with dismissed:
        task_list(ExpertStatus.DISMISSED)


def task_list(status: ExpertStatus) -> None:
    try:
        queue = api.get_brain().expert_tasks(status)
    except api.BrainError as error:
        st.error(str(error))
        return
    if not queue.items:
        st.caption("Nothing here")
    for task in queue.items:  # the most asked first
        asked = "once" if task.asked_count == 1 else f"{task.asked_count} times"
        with st.expander(f"{task.question} (asked {asked})"):
            details(task)
            if status == ExpertStatus.OPEN:
                answer_or_dismiss(task)
            elif status == ExpertStatus.ANSWERED:
                answered(task)
            else:
                st.caption(f"Dismissed by {task.answered_by}, {when(task.answered_at)}")


def details(task: ExpertTaskView) -> None:
    """What the expert needs: why the Brain didn't answer, the chat, and what search found."""
    st.caption(
        f"{REASONS[task.reason]}. First asked {when(task.created_at)}, "
        f"last {when(task.last_asked_at)}."
    )
    for turn in task.history:
        st.markdown(f"> **{turn.role}:** {turn.content}")
    if task.given_answer:
        st.markdown(f"**The answer marked wrong:** {task.given_answer}")
    articles = [source for source in task.retrieved if source.origin == "article"]
    if articles:
        found = "; ".join(f"[{source.title or source.url}]({source.url})" for source in articles)
        st.caption(f"What search found: {found}")


def answer_or_dismiss(task: ExpertTaskView) -> None:
    with st.form(f"answer_{task.id}"):
        answer = st.text_area("Your answer, in Markdown")
        save = st.form_submit_button("Save the answer", type="primary")
    if save:
        if not answer.strip():
            st.error("Write the answer first")
            return
        try:
            api.get_brain().answer_task(task.id, answer, signed_in_user())
        except api.BrainError as error:
            st.error(str(error))
            return
        flash("Saved. The Brain uses it once the worker has embedded it, within a minute.")
        st.rerun()
    if st.button(
        "Dismiss",
        key=f"dismiss_{task.id}",
        help="Off topic for the help center: asked again, it stays out of the queue",
    ):
        try:
            api.get_brain().dismiss_task(task.id, signed_in_user())
        except api.BrainError as error:
            st.error(str(error))
            return
        flash("Dismissed.")
        st.rerun()


def answered(task: ExpertTaskView) -> None:
    st.markdown(f"**Answered by {task.answered_by}, {when(task.answered_at)}:**")
    st.markdown(task.answer or "")
    if task.answer_page_id is not None:
        st.caption(f"Its versions: article {task.answer_page_id} in Change history and archive")
    if st.button(
        "Archive the answer",
        key=f"archive_{task.id}",
        help="Takes it out of search and answers. Answering again brings it back.",
    ):
        try:
            api.get_brain().archive_answer(task.id)
        except api.BrainError as error:
            st.error(str(error))
            return
        flash("Archived: the Brain no longer uses this answer.")
        st.rerun()
