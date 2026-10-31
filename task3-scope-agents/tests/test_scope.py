"""Code-only parts of the scope: pricing, the v2 diff, human edits, and the proposal."""

import pytest

from app.agents import human_approval, proposal_writer
from app.agents.change_analyst import ProposedChange, build_v2, check_changes
from app.agents.splitter import parse_turns
from app.handoffs import FitResult, Hours, ProposalDraft, Quote, Requirement, Scope, Task

# The follow-up meeting the change analyst reads alongside the requirement list
FOLLOWUP = parse_turns(
    "[00:01:03] Priya: We're dropping the blog, please take it out of the project.\n"
    "[00:01:20] Sumit: Okay, blog removed."
)

RATES = {"developer": 50.0, "designer": 45.0, "consultant": 60.0, "qa": 35.0}


def req(rid: str, title: str) -> Requirement:
    return Requirement(
        id=rid,
        title=title,
        detail=f"{title} detail",
        priority="must",
        is_vague=False,
        quotes=[Quote(text=f"we need {title.lower()}", speaker="Priya", time="00:01:00")],
        source_ids=[],
    )


def task(tid: str, rids: list[str], low: float, likely: float, high: float) -> Task:
    hours = Hours(low=low, likely=likely, high=high)
    return Task(
        id=tid,
        requirement_ids=rids,
        title=f"Task {tid}",
        description="",
        role="developer",
        low=low,
        likely=likely,
        high=high,
        rate=50,
        basis="",
        ai_hours=hours,
    )


def v1_scope() -> Scope:
    return Scope(
        version=1,
        requirements=[req("R1", "Search filters"), req("R2", "Blog"), req("R3", "Map")],
        fit=[FitResult(requirement_id="R3", kind="setting", reason="", brain="ok")],
        questions=[],
        tasks=[
            task("T1", ["R1"], 2, 4, 8),
            task("T2", ["R2"], 3, 5, 10),
            task("T3", ["R3"], 0.5, 1, 1),
        ],
        assumptions=["Content comes from the client"],
        exclusions=["Hosting"],
    )


def change(
    label: str, followup: str | None, v1: str | None, quote: str | None = None, **hours: float
) -> ProposedChange:
    return ProposedChange(
        label=label,  # type: ignore[arg-type]
        followup_id=followup,
        v1_requirement_id=v1,
        title=f"{label} item",
        before="before" if v1 else None,
        after="after",
        in_signed_scope=False,
        extra_low=hours.get("low", 0),
        extra_likely=hours.get("likely", 0),
        extra_high=hours.get("high", 0),
        role="developer",
        reason="The client said so.",
        quote=quote,
    )


# --- Pricing ------------------------------------------------------------------------------


def test_expected_hours_weight_likely_four_times() -> None:
    assert Hours(low=2, likely=4, high=12).expected == 5.0  # (2 + 16 + 12) / 6


def test_totals_add_up_tasks_and_price_them() -> None:
    totals = v1_scope().totals

    assert totals.expected_hours == round(4.3 + 5.5 + 0.9, 1)
    assert totals.price == round((4.3 + 5.5 + 0.9) * 50, 2)
    assert totals.price_low == (2 + 3 + 0.5) * 50
    assert totals.change_order_price == 0


# --- Follow-up: the diff ------------------------------------------------------------------


def test_check_changes_drops_made_up_ids_and_keeps_skipped_items() -> None:
    meeting = [req("M1", "Insurance filter"), req("M2", "Booking")]
    proposed = [
        change("changed", "M1", "R1", low=1, likely=2, high=4),
        change("changed", "M9", "R9"),  # points at nothing real: dropped
        change("removed", None, "R2", quote="please take it out of the project"),
    ]

    changes = check_changes(proposed, v1_scope(), meeting, FOLLOWUP)

    assert [(c.label, c.followup_id, c.v1_requirement_id) for c in changes] == [
        ("changed", "M1", "R1"),
        ("removed", None, "R2"),
        ("new", "M2", None),  # skipped by the model: kept, with no hours, for a human
    ]
    assert changes[2].extra_hours is None


def test_a_removal_heard_only_in_the_transcript_keeps_who_said_it_and_when() -> None:
    [removal] = check_changes(
        [change("removed", None, "R2", quote="we're dropping the blog")], v1_scope(), [], FOLLOWUP
    )

    assert (removal.label, removal.v1_requirement_id) == ("removed", "R2")
    assert removal.quote == Quote(text="we're dropping the blog", speaker="Priya", time="00:01:03")


@pytest.mark.parametrize("quote", [None, "Priya said to cancel the blog and the map"])
def test_a_removal_without_the_clients_real_words_is_ignored(quote: str | None) -> None:
    changes = check_changes([change("removed", None, "R2", quote=quote)], v1_scope(), [], FOLLOWUP)

    assert changes == []  # signed work is never removed on the model's word alone


def test_a_change_to_an_unknown_v1_item_becomes_new() -> None:
    [only] = check_changes(
        [change("changed", "M1", "R42", low=1, likely=1, high=2)],
        v1_scope(),
        [req("M1", "Booking")],
        FOLLOWUP,
    )

    assert only.label == "new"
    assert only.in_signed_scope is False


def test_v2_removes_dropped_work_adds_priced_extras_and_leaves_v1_alone() -> None:
    v1 = v1_scope()
    before = v1.model_dump()
    meeting = [req("M1", "Insurance filter"), req("M2", "Online booking"), req("M3", "Map")]
    changes = check_changes(
        [
            change("changed", "M1", "R1", low=1, likely=2, high=4),
            change("new", "M2", None, low=10, likely=16, high=30),
            change("unchanged", "M3", "R3"),
            change("removed", None, "R2", quote="We're dropping the blog"),
        ],
        v1,
        meeting,
        FOLLOWUP,
    )

    v2 = build_v2(v1, meeting, changes, RATES)

    assert v1.model_dump() == before  # v1 is never modified
    assert (v2.version, v2.based_on_version) == (2, 1)
    by_id = {r.id: r for r in v2.requirements}
    assert by_id["R2"].removed and by_id["R2"].change == "removed"
    assert by_id["R1"].change == "changed" and by_id["R1"].detail == "after"
    assert len(by_id["R1"].quotes) == 2  # the old quote and the new one
    assert by_id["R4"].change == "new" and by_id["R4"].title == "Online booking"
    assert by_id["R3"].change == "unchanged"
    # T2 (the blog) is gone; two change tasks are added, both billed as a change order
    assert [t.id for t in v2.tasks] == ["T1", "T3", "T4", "T5"]
    extras = [t for t in v2.tasks if t.change_order]
    assert [t.requirement_ids for t in extras] == [["R1"], ["R4"]]
    assert v2.totals.change_order_hours == round(2.2 + 17.3, 1)
    assert [c.v2_requirement_id for c in v2.changes] == ["R1", "R4", "R3", "R2"]


# --- Human edits --------------------------------------------------------------------------


def test_an_edit_is_recorded_against_the_ai_hours() -> None:
    scope = v1_scope()
    edit = human_approval.ScopeEdit(
        tasks=[
            human_approval.TaskEdit(
                id="T1",
                requirement_ids=["R1"],
                title="Task T1",
                role="developer",
                low=3,
                likely=6,
                high=10,
            ),
            human_approval.TaskEdit(
                id="T3",
                requirement_ids=["R3"],
                title="Task T3",
                role="developer",
                low=0.5,
                likely=1,
                high=1,
            ),
            human_approval.TaskEdit(
                requirement_ids=["R2"],
                title="Write the blog templates",
                role="designer",
                low=1,
                likely=2,
                high=3,
            ),
        ],
        assumptions=["Content comes from the client"],
        exclusions=[],
    )

    edited, record = human_approval.apply_edit(scope, edit, RATES)

    assert [t.id for t in edited.tasks] == ["T1", "T3", "T4"]
    assert edited.tasks[0].ai_hours == Hours(low=2, likely=4, high=8)  # kept for measuring
    assert edited.tasks[2].origin == "human" and edited.tasks[2].rate == 45
    fields = {(c.task_id, c.field) for c in record.changes}
    assert {("T1", "likely"), ("T1", "low"), ("T1", "high"), ("T4", "added")} <= fields
    assert ("T2", "removed") in fields
    assert record.ai_expected_hours == scope.totals.expected_hours
    assert record.expected_hours_after == edited.totals.expected_hours


@pytest.mark.parametrize(
    ("low", "likely", "high", "rids"),
    [(5, 2, 8, ["R1"]), (1, 2, 3, ["R99"])],
)
def test_bad_edits_are_refused(low: float, likely: float, high: float, rids: list[str]) -> None:
    edit = human_approval.ScopeEdit(
        tasks=[
            human_approval.TaskEdit(
                id="T1",
                requirement_ids=rids,
                title="T",
                role="developer",
                low=low,
                likely=likely,
                high=high,
            )
        ],
        assumptions=[],
        exclusions=[],
    )

    with pytest.raises(human_approval.EditError):
        human_approval.apply_edit(v1_scope(), edit, RATES)


# --- The proposal -------------------------------------------------------------------------


def test_a_draft_proposal_says_so_and_takes_its_numbers_from_the_scope() -> None:
    scope = v1_scope()
    scope.proposal = ProposalDraft(
        title="Proposal: Bright Smile directory",
        summary="We'll build it.",
        understanding="You want patients to find dentists.",
        approach="Configure first, code second.",
        settings_note="The map is a built-in setting.",
        timeline="Three phases.",
        next_steps="Approve.",
    )

    draft = proposal_writer.render(scope, "Bright Smile", approved=False)
    final = proposal_writer.render(scope, "Bright Smile", approved=True)

    assert draft.startswith("> **DRAFT")
    assert "DRAFT" not in final
    assert f"${scope.totals.price:,.0f}" in final
    assert "| R3 | Map | Existing setting |" in final
    assert "Change order" not in final


def test_a_v2_proposal_lists_the_changes_and_the_change_order() -> None:
    v1 = v1_scope()
    meeting = [req("M1", "Online booking")]
    changes = check_changes(
        [change("new", "M1", None, low=8, likely=12, high=20)], v1, meeting, FOLLOWUP
    )
    v2 = build_v2(v1, meeting, changes, RATES)

    text = proposal_writer.render(v2, "Bright Smile", approved=True)

    assert "## Changes since version 1" in text
    assert "| New | new item |" in text
    assert f"**Change order:** {v2.totals.change_order_hours:g} expected hours" in text
