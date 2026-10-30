"""Chat: ask the help center, follow-ups included, and see where each answer comes from. A
wrong answer can be flagged for the expert queue."""

import streamlit as st
from pydantic import BaseModel

from app.rag.answer import AskResult, ChatTurn, Citation
from ui import api

TURNS = "chat_turns"  # session state: the conversation so far
# What /v1/ask accepts: a question of up to 2,000 characters, and up to 20 earlier turns of
# up to 4,000 characters each
MAX_QUESTION = 2000
MAX_HISTORY = 20
MAX_TURN = 4000


class Turn(BaseModel):
    question: str
    result: AskResult
    flagged: bool = False  # marked as a wrong answer


def render() -> None:
    st.title("Chat")
    turns: list[Turn] = st.session_state.setdefault(TURNS, [])
    if turns and st.button("New conversation"):
        turns.clear()
        st.rerun()
    for number, turn in enumerate(turns):
        with st.chat_message("user"):
            st.markdown(turn.question)
        with st.chat_message("assistant"):
            show_answer(turn, number)
    question = st.chat_input("Ask about Brilliant Directories", max_chars=MAX_QUESTION)
    if question:
        ask(turns, question)


def ask(turns: list[Turn], question: str) -> None:
    """Send the question with the conversation so far, so a follow-up can be understood."""
    history: list[ChatTurn] = []
    for turn in turns:
        history.append(ChatTurn(role="user", content=turn.question[:MAX_TURN]))
        history.append(ChatTurn(role="assistant", content=turn.result.answer[:MAX_TURN]))
    try:
        with st.spinner("Searching the help center"):
            result = api.get_brain().ask(question, history[-MAX_HISTORY:])
    except api.BrainError as error:
        st.error(str(error))
        return
    turns.append(Turn(question=question, result=result))
    st.rerun()


def show_answer(turn: Turn, number: int) -> None:
    result = turn.result
    st.markdown(result.answer)
    if not result.answered:
        if result.expert_task_id is not None:
            st.caption(
                f"Sent to an expert as task {result.expert_task_id}. Once they answer, the "
                "Brain uses their answer."
            )
        return
    for citation in result.citations:
        cite(citation)
    if turn.flagged:
        st.caption("Marked as wrong: it's in the expert queue.")
    elif st.button("Wrong answer", key=f"flag_{number}", icon=":material/thumb_down:"):
        try:
            api.get_brain().flag(result.standalone_question, result.answer)
        except api.BrainError as error:
            st.error(str(error))
            return
        turn.flagged = True
        st.rerun()


def cite(citation: Citation) -> None:
    """A source the answer cites: an article link, its screenshot if that's the source, or an
    expert's answer, which has no page on the help center to link to."""
    if citation.origin == "expert":
        st.caption(f"{citation.source_id}: expert answer. {citation.snippet}")
        return
    st.caption(f"{citation.source_id}: [{citation.title or citation.url}]({citation.url})")
    if citation.image_url:
        st.image(citation.image_url, width=360)
