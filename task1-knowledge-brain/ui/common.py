"""Small helpers the screens share."""

from datetime import datetime
from typing import Literal

import streamlit as st

FLASH = "flash"  # session state: a message to show once, after the next rerun


def when(moment: datetime | None) -> str:
    """A time from the API, which are all in UTC, the way the screens show it."""
    return f"{moment:%b %d, %H:%M} UTC" if moment else "never"


def table(rows: list[dict[str, object]]) -> None:
    """Rows as a table, without pandas' index column."""
    # st.dataframe's type lists data types from a library that ships no type hints
    st.dataframe(rows, hide_index=True)  # pyright: ignore[reportUnknownMemberType]


def signed_in_user() -> str:
    """Who is signed in. The screens run only after the sign-in, so there always is someone."""
    return str(st.session_state["user"])


def flash(message: str, kind: Literal["success", "error"] = "success") -> None:
    """Keep a message for the page to show after the rerun that an action ends with."""
    st.session_state[FLASH] = (kind, message)


def show_flash() -> None:
    if FLASH in st.session_state:
        kind, message = st.session_state.pop(FLASH)
        if kind == "success":
            st.success(message)
        else:
            st.error(message)
