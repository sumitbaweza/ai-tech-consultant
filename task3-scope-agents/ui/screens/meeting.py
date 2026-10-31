"""New meeting: pick a project, pick or upload a transcript, start the agent team."""

import streamlit as st

from app.api.views import ProjectView
from ui.api import ScopeAPIError, get_api
from ui.common import PAGES, sample_transcripts, show_error


def _label(project: ProjectView) -> str:
    approved = [v.version for v in project.versions if v.status == "approved"]
    state = f"v{approved[-1]} approved" if approved else "no approved scope yet"
    return f"{project.name} · {project.client} ({state})"


def render() -> None:
    st.title("New meeting")
    st.caption(
        "A project's first meeting runs the full 8-step team and ends in scope v1. Once a "
        "version is approved, the next meeting is a follow-up, compared against it."
    )
    api = get_api()
    try:
        projects = api.projects()
    except ScopeAPIError as error:
        show_error(error)
        return

    st.subheader("Project")
    choice = st.radio(
        "Project",
        ["Existing project", "New project"],
        horizontal=True,
        label_visibility="collapsed",
        index=0 if projects else 1,
    )
    project: ProjectView | None = None
    name = client = ""
    if choice == "Existing project":
        if not projects:
            st.info("No projects yet: create one.")
            return
        project = st.selectbox("Project", projects, format_func=_label)
        if any(v.status == "approved" for v in project.versions):
            st.info(
                "This meeting will be a **follow-up**: steps 1 to 3, then the change "
                "analyst compares it with the approved scope.",
                icon=":material/difference:",
            )
    else:
        name = st.text_input("Project name", placeholder="Directory website relaunch")
        client = st.text_input("Client", placeholder="Acme Dental Network")

    st.subheader("Transcript")
    source = st.radio(
        "Source", ["Sample", "Upload", "Paste"], horizontal=True, label_visibility="collapsed"
    )
    transcript = ""
    title = ""
    if source == "Sample":
        samples = sample_transcripts()
        if samples:
            picked = st.selectbox("Stand-in transcript", list(samples))
            if picked:
                transcript, title = samples[picked], picked.removesuffix(".txt")
        else:
            st.warning("No sample transcripts in transcripts/.")
    elif source == "Upload":
        uploaded = st.file_uploader("Transcript file", type=["txt", "vtt", "md"])
        if uploaded is not None:
            transcript = uploaded.getvalue().decode("utf-8", errors="replace")
            title = uploaded.name.rsplit(".", 1)[0]
    else:
        transcript = st.text_area(
            "Transcript", height=300, placeholder="[00:00:05] Client: We need ..."
        )
    with st.expander("Preview", expanded=False):
        st.text(transcript[:5000] or "(empty)")
    title = st.text_input("Meeting title", value=title)

    if st.button("Start the agents", type="primary", disabled=not transcript.strip()):
        try:
            if project is None:
                if not name.strip() or not client.strip():
                    st.error("Give the new project a name and a client.")
                    return
                project = api.create_project(name.strip(), client.strip())
            run = api.add_meeting(project.id, title.strip() or "Meeting", transcript)
        except ScopeAPIError as error:
            show_error(error)
            return
        st.session_state["run_id"] = run.id
        st.switch_page(PAGES["run"])
