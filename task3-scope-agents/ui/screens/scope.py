"""Scope and approval: edit hours and scope, approve or send back, compare versions, and see
the proposal. Nothing reaches the client before a human approves it here."""

from typing import Any

import streamlit as st

from app.agents.human_approval import TaskEdit
from app.api.views import ProjectView, VersionView
from app.handoffs import Scope
from ui.api import ScopeAPIError, get_api
from ui.common import CHANGE_COLORS, FIT_COLORS, PAGES, badge, reviewer_name, show_error, table

ROLES = ["developer", "designer", "consultant", "qa"]


def _pick_version(projects: list[ProjectView]) -> int | None:
    versions = [(p, v) for p in projects for v in reversed(p.versions)]
    if not versions:
        st.info("No scope versions yet. A run makes one once its agents finish.")
        return None
    ids = [v.id for _, v in versions]
    wanted = st.session_state.get("version_id")
    labels = {v.id: f"{p.name} · v{v.version} · {v.status} · ${v.price:,.0f}" for p, v in versions}
    chosen = st.selectbox(
        "Scope version",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=lambda i: labels[i],
    )
    st.session_state["version_id"] = chosen
    return chosen


def render() -> None:
    st.title("Scope and approval")
    api = get_api()
    try:
        projects = api.projects()
        version_id = _pick_version(projects)
        if version_id is None:
            return
        version = api.version(version_id)
    except ScopeAPIError as error:
        show_error(error)
        return

    scope = version.scope
    totals = scope.totals
    st.markdown(f"Version {version.version} · {badge(version.status)}")
    columns = st.columns(4)
    columns[0].metric("Expected hours", f"{totals.expected_hours:g}")
    columns[1].metric("Price", f"${totals.price:,.0f}")
    columns[2].metric("Range", f"${totals.price_low / 1000:.1f}k-{totals.price_high / 1000:.1f}k")
    columns[3].metric("Change order", f"${totals.change_order_price:,.0f}")
    if version.decided_by:
        st.caption(f"{version.status.capitalize()} by {version.decided_by} · {version.note or ''}")

    tabs = st.tabs(["Estimate", "Requirements", "Changes vs previous", "Proposal", "Decision"])
    editable = version.status == "draft"
    with tabs[0]:
        _estimate(version, editable)
    with tabs[1]:
        _requirements(scope)
    with tabs[2]:
        _compare(version, projects)
    with tabs[3]:
        _proposal(version)
    with tabs[4]:
        _decision(version, editable)


def _estimate(version: VersionView, editable: bool) -> None:
    scope = version.scope
    if scope.review is not None:
        color = "green" if scope.review.verdict == "approve" else "orange"
        st.markdown(f"Senior review: :{color}-badge[{scope.review.verdict}] {scope.review.summary}")
        for issue in scope.review.issues:
            st.caption(f"{issue.target}: {issue.problem} Fix: {issue.fix}")
    rows: list[dict[str, Any]] = [
        {
            "id": t.id,
            "requirements": ", ".join(t.requirement_ids),
            "title": t.title,
            "role": t.role,
            "low": t.low,
            "likely": t.likely,
            "high": t.high,
            "change_order": t.change_order,
            "AI likely": t.ai_hours.likely if t.ai_hours else None,
            "price": t.price,
        }
        for t in scope.tasks
    ]
    config = {
        "id": st.column_config.TextColumn(disabled=True),
        "role": st.column_config.SelectboxColumn(options=ROLES, required=True),
        "low": st.column_config.NumberColumn(min_value=0.0, step=0.5),
        "likely": st.column_config.NumberColumn(min_value=0.0, step=0.5),
        "high": st.column_config.NumberColumn(min_value=0.0, step=0.5),
        "AI likely": st.column_config.NumberColumn(disabled=True, help="The estimator's hours"),
        "price": st.column_config.NumberColumn(disabled=True, format="$%.0f"),
    }
    if not editable:
        table(rows, column_config=config)
        _lists(scope, editable=False)
        return

    st.caption(
        "Edit hours, roles and titles; add or delete rows. Requirement ids are comma-separated."
    )
    edited = st.data_editor(
        rows, column_config=config, num_rows="dynamic", key=f"tasks-{version.id}"
    )
    assumptions, exclusions = _lists(scope, editable=True)
    if st.button("Save edits"):
        if not reviewer_name():
            st.error("Enter your name in the sidebar first.")
            return
        try:
            tasks = [_task_edit(row) for row in edited]
            get_api().edit(version.id, reviewer_name(), tasks, assumptions, exclusions)
        except (ScopeAPIError, ValueError) as error:
            st.error(str(error))
            return
        st.rerun()


def _task_edit(row: dict[str, Any]) -> TaskEdit:
    ids = [i.strip() for i in str(row.get("requirements") or "").split(",") if i.strip()]
    return TaskEdit(
        id=row.get("id") or None,
        requirement_ids=ids,
        title=str(row.get("title") or ""),
        role=row.get("role") or "developer",
        low=float(row.get("low") or 0),
        likely=float(row.get("likely") or 0),
        high=float(row.get("high") or 0),
        change_order=bool(row.get("change_order")),
    )


def _lists(scope: Scope, *, editable: bool) -> tuple[list[str], list[str]]:
    if not editable:
        st.markdown("**Assumptions**\n" + "\n".join(f"- {a}" for a in scope.assumptions))
        st.markdown("**Not included**\n" + "\n".join(f"- {e}" for e in scope.exclusions))
        return scope.assumptions, scope.exclusions
    left, right = st.columns(2)
    assumptions = left.text_area("Assumptions, one per line", "\n".join(scope.assumptions))
    exclusions = right.text_area("Not included, one per line", "\n".join(scope.exclusions))
    return (
        [line.strip() for line in assumptions.splitlines() if line.strip()],
        [line.strip() for line in exclusions.splitlines() if line.strip()],
    )


def _requirements(scope: Scope) -> None:
    fit = {f.requirement_id: f for f in scope.fit}
    for r in scope.requirements:
        result = fit.get(r.id)
        kind = result.kind if result else "unverified"
        change = f" :{CHANGE_COLORS[r.change]}-badge[{r.change}]" if r.change else ""
        title = f"~~{r.title}~~" if r.removed else r.title
        st.markdown(f":{FIT_COLORS[kind]}-badge[{kind}]{change} **{r.id} · {title}** · {r.detail}")
        quote = r.quotes[0]
        st.caption(f'[{quote.time or "?"}] {quote.speaker}: "{quote.text}"')
        if result and result.how_to:
            st.caption(f"Setting: {result.setting_path or ''}. {result.how_to}")
    if scope.questions:
        st.subheader("Clarifying questions")
        for q in scope.questions:
            st.markdown(f"**{q.id}.** {q.question}  \n*{q.resolution}*")


def _compare(version: VersionView, projects: list[ProjectView]) -> None:
    scope = version.scope
    if not scope.changes:
        st.info("This is a first version: there is nothing to compare it with yet.")
        return
    project = next(p for p in projects if p.id == version.project_id)
    previous = next((v for v in project.versions if v.version == scope.based_on_version), None)
    if previous is not None:
        left, right = st.columns(2)
        left.metric(f"v{previous.version} price", f"${previous.price:,.0f}")
        right.metric(
            f"v{version.version} price",
            f"${version.price:,.0f}",
            delta=f"{version.price - previous.price:+,.0f}",
        )
    for change in scope.changes:
        color = CHANGE_COLORS[change.label]
        hours = f" · +{change.extra_hours.expected:g} h" if change.extra_hours else ""
        billed = ""
        if change.label in ("changed", "new"):
            billed = " · within the agreed scope" if change.in_signed_scope else " · change order"
        st.markdown(f":{color}-badge[{change.label}] **{change.title}**{hours}{billed}")
        st.caption(f"Before: {change.before or '-'} → Now: {change.after or '-'} · {change.reason}")


def _proposal(version: VersionView) -> None:
    if version.status == "draft":
        st.warning("Draft: this preview can't be exported until a human approves the version.")
    else:
        try:
            markdown = get_api().proposal(version.id)
        except ScopeAPIError as error:
            show_error(error)
            return
        st.download_button(
            "Download the proposal (Markdown)",
            markdown,
            file_name=f"proposal-v{version.version}.md",
            mime="text/markdown",
            type="primary",
        )
    with st.container(border=True):
        st.markdown(version.proposal_markdown)


def _decision(version: VersionView, editable: bool) -> None:
    if not editable:
        st.info(
            f"This version is {version.status}: it can't change. A follow-up meeting makes "
            "the next version."
        )
        return
    note = st.text_area("Note", help="Required to send back: the agents get it as is")
    left, right = st.columns(2)
    if left.button("Approve", type="primary", use_container_width=True):
        if not reviewer_name():
            st.error("Enter your name in the sidebar first.")
            return
        try:
            get_api().approve(version.id, reviewer_name(), note or None)
        except ScopeAPIError as error:
            show_error(error)
            return
        st.rerun()
    if right.button("Send back to the agents", use_container_width=True):
        if not reviewer_name() or not note.strip():
            st.error("Enter your name in the sidebar and a note saying what to fix.")
            return
        try:
            run = get_api().send_back(version.id, reviewer_name(), note)
        except ScopeAPIError as error:
            show_error(error)
            return
        st.session_state["run_id"] = run.id
        st.switch_page(PAGES["run"])
