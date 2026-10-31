"""Small pieces the screens share."""

from pathlib import Path
from typing import Any

import streamlit as st
from streamlit.navigation.page import StreamlitPage

from ui.api import ScopeAPIError

# The app's pages by url path, filled in by ui/main.py, so one screen can open another
PAGES: dict[str, StreamlitPage] = {}

TRANSCRIPTS_DIR = Path(__file__).resolve().parents[1] / "transcripts"

AGENT_TITLES = {
    "splitter": "1 · Splitter",
    "extractor": "2 · Extractor (Haiku)",
    "merger": "3 · Merger (Sonnet)",
    "fit_checker": "4 · Fit checker (Sonnet + Knowledge Brain)",
    "consultant": "5 · Consultant (Sonnet)",
    "human_answers": "Human · answers to the questions",
    "estimator": "6 · Estimator (Sonnet)",
    "reviewer": "7 · Reviewer (Opus)",
    "change_analyst": "Change analyst (Sonnet)",
    "proposal_writer": "8 · Proposal writer (Sonnet)",
    "human_edit": "Human · edits",
    "human_send_back": "Human · sent back",
    "human_approval": "Human · approved",
    "learner": "Learner (Sonnet): estimation rules from the edits",
}

STATUS_BADGES = {
    "queued": ":gray-badge[queued]",
    "running": ":blue-badge[running]",
    "awaiting_answers": ":orange-badge[waiting for answers]",
    "awaiting_approval": ":orange-badge[waiting for approval]",
    "approved": ":green-badge[approved]",
    "failed": ":red-badge[failed]",
    "draft": ":orange-badge[draft]",
    "superseded": ":gray-badge[superseded]",
    "done": ":green-badge[done]",
}

CHANGE_COLORS = {"unchanged": "gray", "changed": "orange", "new": "blue", "removed": "red"}
FIT_COLORS = {"setting": "green", "small_change": "blue", "custom": "violet", "unverified": "gray"}


def badge(status: str) -> str:
    return STATUS_BADGES.get(status, f":gray-badge[{status}]")


def reviewer_name() -> str:
    """Who is at the screen: recorded on every answer, edit, approval and send-back."""
    name: str = st.session_state.get("reviewer", "")
    return name.strip()


def sidebar() -> None:
    with st.sidebar:
        st.text_input(
            "Your name",
            key="reviewer",
            help="Recorded on every answer, edit, approval and send-back",
        )
        if not reviewer_name():
            st.caption("Enter your name to answer questions, edit and approve.")


def show_error(error: ScopeAPIError) -> None:
    st.error(str(error), icon=":material/error:")


def sample_transcripts() -> dict[str, str]:
    """The stand-in transcripts shipped in transcripts/, by file name."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TRANSCRIPTS_DIR.glob("*.txt"))
    }


def table(rows: list[dict[str, Any]], column_config: dict[str, Any] | None = None) -> None:
    """A read-only table. st.dataframe's type lists data types from a library that ships no
    type hints, hence the one ignore."""
    st.dataframe(  # pyright: ignore[reportUnknownMemberType]
        rows, hide_index=True, column_config=column_config
    )
