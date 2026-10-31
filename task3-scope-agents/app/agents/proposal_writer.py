"""Step 8, the proposal writer (Sonnet 5.5): a client-ready proposal from the scope.

Sonnet writes only the words: summary, the client's goals, approach, what existing settings
save them, timeline and next steps. Code renders every number (tasks, hours, prices, totals,
the change order) straight from the Scope, so the proposal can never quote a figure the
estimate doesn't hold, and a human's edits to the hours show up without rewriting the text.
"""

import json

from app.handoffs import ProposalDraft, Scope
from app.llm import Claude
from app.text import as_data

PROMPT, VERSION = "write_proposal", 1

FIT_LABELS = {
    "setting": "Existing setting",
    "small_change": "Setting plus small change",
    "custom": "Custom work",
    "unverified": "Custom work (not checked against settings)",
}
CHANGE_LABELS = {"unchanged": "Unchanged", "changed": "Changed", "new": "New", "removed": "Removed"}


def _content(scope: Scope, project: str, client: str) -> str:
    fit = {result.requirement_id: result for result in scope.fit}
    brief = {
        "project": project,
        "client": client,
        "version": scope.version,
        "requirements": [
            {
                "id": r.id,
                "title": r.title,
                "detail": r.detail,
                "fit": fit[r.id].kind if r.id in fit else "unverified",
                "setting": fit[r.id].setting_path if r.id in fit else None,
                "removed": r.removed,
            }
            for r in scope.requirements
        ],
        "tasks": [t.model_dump(include={"title", "role", "expected"}) for t in scope.tasks],
        "assumptions": scope.assumptions,
        "exclusions": scope.exclusions,
        "changes": [
            c.model_dump(include={"label", "title", "after", "in_signed_scope"})
            for c in scope.changes
        ],
        "totals": scope.totals.model_dump(),
    }
    return "The approved facts for this proposal:\n\n" + as_data(
        "scope", json.dumps(brief, indent=1)
    )


async def write(
    claude: Claude, scope: Scope, project: str, client: str, run_tag: str | None = None
) -> ProposalDraft:
    return await claude.call(
        PROMPT, VERSION, _content(scope, project, client), ProposalDraft, run_tag=run_tag
    )


def _money(amount: float) -> str:
    return f"${amount:,.0f}"


def _cell(text: str) -> str:
    """Text safe inside a Markdown table cell."""
    return text.replace("|", "/").replace("\n", " ")


def render(scope: Scope, client: str, *, approved: bool) -> str:
    """The proposal as Markdown. Until a human approves it, it says DRAFT at the top."""
    draft = scope.proposal or ProposalDraft(
        title=f"Proposal for {client}",
        summary="",
        understanding="",
        approach="",
        settings_note="",
        timeline="",
        next_steps="",
    )
    totals = scope.totals
    lines: list[str] = []
    if not approved:
        lines += ["> **DRAFT: not approved. Do not send this to the client.**", ""]
    lines += [f"# {draft.title}", "", f"Prepared for {client} · Scope version {scope.version}", ""]
    for heading, body in [
        ("Summary", draft.summary),
        ("What you told us", draft.understanding),
        ("How we'll do it", draft.approach),
        ("What your current settings already cover", draft.settings_note),
    ]:
        if body:
            lines += [f"## {heading}", "", body, ""]

    fit = {result.requirement_id: result for result in scope.fit}
    lines += ["## Scope", "", "| # | Requirement | How | Notes |", "| --- | --- | --- | --- |"]
    for r in scope.requirements:
        result = fit.get(r.id)
        how = FIT_LABELS[result.kind] if result else FIT_LABELS["unverified"]
        note = (result.setting_path or "") if result else ""
        if r.change:
            note = f"{CHANGE_LABELS[r.change]} in v{scope.version}. {note}".strip()
        title = f"~~{r.title}~~" if r.removed else r.title
        lines.append(f"| {r.id} | {_cell(title)} | {how} | {_cell(note)} |")

    lines += [
        "",
        "## Estimate",
        "",
        "| Task | Role | Hours (low / likely / high) | Expected hours | Price |",
        "| --- | --- | --- | --- | --- |",
    ]
    for t in scope.tasks:
        extra = " *(change order)*" if t.change_order else ""
        lines.append(
            f"| {_cell(t.title)}{extra} | {t.role} | {t.low:g} / {t.likely:g} / {t.high:g} "
            f"| {t.expected:g} | {_money(t.price)} |"
        )
    lines += [
        f"| **Total** | | {totals.low_hours:g} / - / {totals.high_hours:g} "
        f"| **{totals.expected_hours:g}** | **{_money(totals.price)}** |",
        "",
        f"Expected price {_money(totals.price)}, within a range of {_money(totals.price_low)} "
        f"to {_money(totals.price_high)}. Expected hours weight the likely estimate four times "
        "its low and high ends.",
        "",
    ]

    if scope.changes:
        lines += [
            f"## Changes since version {scope.based_on_version}",
            "",
            "| Change | Item | Before | Now | Quoted extra |",
            "| --- | --- | --- | --- | --- |",
        ]
        for change in scope.changes:
            extra = "No (within the agreed scope)" if change.in_signed_scope else "Yes"
            lines.append(
                f"| {CHANGE_LABELS[change.label]} | {_cell(change.title)} "
                f"| {_cell(change.before or '-')} | {_cell(change.after or '-')} | {extra} |"
            )
        lines += [
            "",
            f"**Change order:** {totals.change_order_hours:g} expected hours, "
            f"{_money(totals.change_order_price)}, for work outside the approved scope.",
            "",
        ]

    for heading, items in [("Assumptions", scope.assumptions), ("Not included", scope.exclusions)]:
        if items:
            lines += [f"## {heading}", "", *(f"- {item}" for item in items), ""]
    for heading, body in [("Timeline", draft.timeline), ("Next steps", draft.next_steps)]:
        if body:
            lines += [f"## {heading}", "", body, ""]
    return "\n".join(lines)
