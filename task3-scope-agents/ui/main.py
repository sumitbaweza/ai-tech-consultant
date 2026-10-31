"""The scope team's screens: the scope-ui service, at http://localhost:8502.

    uv run python -m streamlit run ui/main.py

Every screen talks to the scope API (ui/api.py), never to the database.
"""

import streamlit as st

from ui.common import PAGES, sidebar
from ui.screens import meeting, run, scope

st.set_page_config(page_title="Meeting to Scope", layout="wide")
sidebar()

pages = [
    st.Page(meeting.render, title="New meeting", icon=":material/upload_file:", url_path="new"),
    st.Page(run.render, title="Agent run", icon=":material/smart_toy:", url_path="run"),
    st.Page(
        scope.render, title="Scope and approval", icon=":material/fact_check:", url_path="scope"
    ),
]
PAGES.update({page.url_path: page for page in pages})
st.navigation(pages).run()
