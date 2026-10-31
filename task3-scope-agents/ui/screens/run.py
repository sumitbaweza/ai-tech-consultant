"""Agent run: watch each agent's output as it arrives, answer the consultant's questions."""

from typing import Any

import streamlit as st

from app.agents.estimator import EstimateResult
from app.agents.extractor import ExtractionResult
from app.api.views import RunView, StepView
from app.handoffs import ChangeItem, FitResult, ProposalDraft, Question, Requirement, Review, Task
from app.orchestrator import Changes, Fits, Questions, Requirements, Split
from ui.api import ScopeAPIError, get_api
from ui.common import (
    AGENT_TITLES,
    CHANGE_COLORS,
    FIT_COLORS,
    PAGES,
    badge,
    reviewer_name,
    show_error,
    table,
)

LIVE = ("queued", "running")


def _pick_run() -> int | None:
    """Choose a run; the one just started is selected."""
    api = get_api()
    try:
        projects = api.projects()
    except ScopeAPIError as error:
        show_error(error)
        return None
    runs = [(p, r) for p in projects for r in reversed(p.runs)]
    if not runs:
        st.info("No runs yet. Start one from **New meeting**.")
        return None
    ids = [r.id for _, r in runs]
    wanted = st.session_state.get("run_id")
    index = ids.index(wanted) if wanted in ids else 0
    labels = {r.id: f"Run {r.id} · {p.name} · {r.meeting_title} ({r.kind})" for p, r in runs}
    chosen = st.selectbox("Run", ids, index=index, format_func=lambda i: labels[i])
    st.session_state["run_id"] = chosen
    return chosen


def render() -> None:
    st.title("Agent run")
    run_id = _pick_run()
    if run_id is None:
        return
    api = get_api()
    try:
        first = api.run(run_id)
    except ScopeAPIError as error:
        show_error(error)
        return
    was_live = first.status in LIVE

    @st.fragment(run_every=3 if was_live else None)
    def live() -> None:
        try:
            run = api.run(run_id)
        except ScopeAPIError as error:
            show_error(error)
            return
        if was_live and run.status not in LIVE:
            st.rerun()  # stop refreshing, and show the questions or the next step
        _show(run)

    live()


def _show(run: RunView) -> None:
    cost = f"${run.total_cost:.4f}"
    st.markdown(f"**{run.meeting_title}** · {run.kind} meeting · {badge(run.status)} · {cost}")
    if run.status == "failed":
        st.error(run.error or "The run failed.")
        if st.button("Retry", type="primary"):
            _act(lambda: get_api().retry(run.id))
    elif run.status in LIVE:
        st.info("The agents are working. This page refreshes by itself.", icon=":material/sync:")
    elif run.status == "awaiting_answers":
        _questions_form(run)
    elif run.version is not None:
        st.success(
            f"Draft scope v{run.version.version}: {run.version.expected_hours:g} expected hours, "
            f"${run.version.price:,.0f}. A human approves it before it goes anywhere."
        )
        if st.button("Open scope and approval", type="primary"):
            st.session_state["version_id"] = run.version.id
            st.switch_page(PAGES["scope"])

    for step in reversed(run.steps):
        title = AGENT_TITLES.get(step.agent, step.agent)
        spent = run.costs.get(step.agent)
        suffix = f" · ${spent:.4f} for this agent" if spent else ""
        with st.expander(f"{title} · {step.status}{suffix}", expanded=False):
            if step.error:
                st.error(step.error)
            if step.output is not None:
                _render_output(step)


def _act(action: Any) -> None:
    try:
        action()
    except ScopeAPIError as error:
        show_error(error)
        return
    st.rerun()


def _questions_form(run: RunView) -> None:
    asked = next((s for s in reversed(run.steps) if s.agent == "consultant"), None)
    if asked is None or asked.output is None:
        return
    questions = Questions.model_validate(asked.output).questions
    st.warning(
        "The consultant has questions for the client. Answer what you know; anything left "
        "blank becomes a written assumption in the proposal.",
        icon=":material/help:",
    )
    with st.form("answers"):
        answers: dict[str, str] = {}
        for q in questions:
            about = f" (about {q.requirement_id})" if q.requirement_id else ""
            answers[q.id] = st.text_area(
                f"**{q.id}{about}.** {q.question}",
                help=f"Why it matters: {q.why_it_matters}",
                placeholder=f"If blank: {q.default_assumption}",
            )
        sent = st.form_submit_button("Continue to the estimate", type="primary")
    if sent:
        if not reviewer_name():
            st.error("Enter your name in the sidebar first.")
            return
        _act(lambda: get_api().answer(run.id, reviewer_name(), answers))


# --- One renderer per agent ---------------------------------------------------------------


def _render_output(step: StepView) -> None:
    output = step.output or {}
    match step.agent:
        case "splitter":
            chunks = Split.model_validate(output).chunks
            st.write(f"{len(chunks)} chunks, split between speaker turns.")
            table(
                [
                    {"chunk": c.index + 1, "from": c.start, "to": c.end, "turns": len(c.turns)}
                    for c in chunks
                ],
            )
        case "extractor":
            result = ExtractionResult.model_validate(output)
            table(
                [
                    {
                        "id": i.id,
                        "title": i.title,
                        "quote": i.quote.text,
                        "who": i.quote.speaker,
                        "when": i.quote.time,
                        "priority": i.priority,
                        "vague": i.is_vague,
                    }
                    for i in result.items
                ],
            )
            if result.dropped:
                st.warning(
                    f"Dropped {len(result.dropped)} items whose quote isn't in the transcript: "
                    + "; ".join(f'{d.title} ("{d.quote}")' for d in result.dropped)
                )
        case "merger":
            _requirements(Requirements.model_validate(output).requirements)
        case "fit_checker":
            for result in Fits.model_validate(output).results:
                _fit(result)
        case "consultant" | "human_answers":
            _question_list(Questions.model_validate(output).questions)
        case "estimator":
            estimate = EstimateResult.model_validate(output)
            _tasks(estimate.tasks)
            st.markdown("**Assumptions:** " + "; ".join(estimate.assumptions))
            st.markdown("**Exclusions:** " + "; ".join(estimate.exclusions))
        case "reviewer":
            _review(Review.model_validate(output))
        case "change_analyst":
            for change in Changes.model_validate(output).changes:
                _change(change)
        case "proposal_writer":
            draft = ProposalDraft.model_validate(output)
            st.markdown(f"**{draft.title}**\n\n{draft.summary}")
        case _:
            st.json(output)


def _requirements(requirements: list[Requirement]) -> None:
    table(
        [
            {
                "id": r.id,
                "title": r.title,
                "detail": r.detail,
                "priority": r.priority,
                "vague": r.is_vague,
                "conflict": r.conflict or "",
                "quotes": " / ".join(f"[{q.time or '?'}] {q.speaker}: {q.text}" for q in r.quotes),
            }
            for r in requirements
        ],
    )


def _fit(result: FitResult) -> None:
    color = FIT_COLORS[result.kind]
    links = ", ".join(f"[{a.title or a.url}]({a.url})" for a in result.articles)
    path = f" · *{result.setting_path}*" if result.setting_path else ""
    st.markdown(
        f":{color}-badge[{result.kind}] **{result.requirement_id}**{path} · {result.reason}"
        + (f"  \nSources: {links}" if links else "")
    )
    if result.how_to:
        st.caption(f"How to set it up, from the Brain: {result.how_to}")


def _question_list(questions: list[Question]) -> None:
    if not questions:
        st.write("No questions: the requirements are clear enough to price.")
    for q in questions:
        st.markdown(f"**{q.id}.** {q.question}  \n*{q.resolution}*")


def _tasks(tasks: list[Task]) -> None:
    table(
        [
            {
                "id": t.id,
                "for": ", ".join(t.requirement_ids),
                "title": t.title,
                "role": t.role,
                "low": t.low,
                "likely": t.likely,
                "high": t.high,
                "expected": t.expected,
                "price": t.price,
                "basis": t.basis,
            }
            for t in tasks
        ],
        column_config={"price": st.column_config.NumberColumn(format="$%.0f")},
    )


def _review(review: Review) -> None:
    color = "green" if review.verdict == "approve" else "orange"
    st.markdown(f":{color}-badge[{review.verdict}] {review.summary}")
    if review.issues:
        table([i.model_dump() for i in review.issues])


def _change(change: ChangeItem) -> None:
    color = CHANGE_COLORS[change.label]
    hours = f" · +{change.extra_hours.expected:g} h" if change.extra_hours else ""
    scope = (
        ""
        if change.label in ("unchanged", "removed")
        else (" · within the agreed scope" if change.in_signed_scope else " · **change order**")
    )
    st.markdown(f":{color}-badge[{change.label}] **{change.title}**{hours}{scope}")
    if change.before or change.after:
        st.caption(f"Before: {change.before or '-'} → Now: {change.after or '-'}")
    if change.quote:
        st.caption(f'[{change.quote.time or "?"}] {change.quote.speaker}: "{change.quote.text}"')
