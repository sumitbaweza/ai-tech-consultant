"""Learning: the estimation playbook, whether it shrinks the hours gap, and rollback."""

import streamlit as st

from ui.api import ScopeAPIError, get_api
from ui.common import show_error, table


def render() -> None:
    st.title("Learning")
    st.caption(
        "When you edit hours before approving, the learner turns the edits into estimation "
        "rules. Each update is a new playbook version; the estimator follows the active one. "
        "If a version makes estimates worse, make an older one active."
    )
    api = get_api()
    try:
        playbook = api.playbook()
    except ScopeAPIError as error:
        show_error(error)
        return

    st.subheader("Is it helping?")
    if playbook.gaps:
        table(
            [
                {
                    "playbook": f"v{g.playbook_version}" if g.playbook_version else "none",
                    "approved scopes": g.scopes,
                    "AI hours": g.ai_hours,
                    "approved hours": g.approved_hours,
                    "gap %": g.gap_percent,
                }
                for g in playbook.gaps
            ]
        )
        st.caption("Gap: how far the AI's hours were from the approved ones. Lower is better.")
    else:
        st.info("No approved scopes yet.")

    st.subheader("Playbook versions")
    if not playbook.versions:
        st.info("Nothing learned yet: approve a scope after editing its hours.")
    for version in reversed(playbook.versions):
        label = f"v{version.version}" + (" · active" if version.is_active else "")
        with st.expander(label, expanded=version.is_active):
            for rule in version.rules:
                role = f" ({rule.role})" if rule.role else ""
                st.markdown(f"- {rule.text}{role}  \n  *{rule.evidence}*")
            if not version.is_active and st.button(
                f"Make v{version.version} active", key=f"activate-{version.version}"
            ):
                try:
                    api.activate(version.version)
                except ScopeAPIError as error:
                    show_error(error)
                    return
                st.rerun()
