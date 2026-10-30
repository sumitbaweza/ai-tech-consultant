"""The Knowledge Brain's screens: the brain-ui service, at http://localhost:8501.

    uv run python -m streamlit run ui/main.py

Every screen sits behind the sign-in, as the brief requires, and talks to the Brain's API
(ui/api.py), never to its database.
"""

import streamlit as st

from ui.passwords import sign_in, users
from ui.screens import chat, dashboard, experts, history, sources

st.set_page_config(page_title="Knowledge Brain", layout="wide")


def sign_in_screen() -> None:
    st.title("Knowledge Brain")
    if not users():
        st.warning(
            "Nobody can sign in yet. Add a user to BRAIN_UI_USERS in .env: "
            "`uv run python -m ui.passwords <name>` makes the entry."
        )
    with st.form("sign_in"):
        name = st.text_input("Name")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if sign_in(name, password):
            st.session_state["user"] = name
            st.rerun()  # draw the app, now signed in
        st.error("Wrong name or password")


user: str | None = st.session_state.get("user")
if user is None:
    sign_in_screen()
    st.stop()

with st.sidebar:
    st.caption(f"Signed in as **{user}**")
    if st.button("Sign out"):
        del st.session_state["user"]
        st.rerun()

pages = [
    st.Page(
        dashboard.render,
        title="Progress and dashboard",
        icon=":material/monitoring:",
        url_path="dashboard",
        default=True,
    ),
    st.Page(sources.render, title="Sources", icon=":material/travel_explore:", url_path="sources"),
    st.Page(chat.render, title="Chat", icon=":material/chat:", url_path="chat"),
    st.Page(
        experts.render, title="Expert queue", icon=":material/support_agent:", url_path="experts"
    ),
    st.Page(
        history.render,
        title="Change history and archive",
        icon=":material/history:",
        url_path="history",
    ),
]
st.navigation(pages).run()
