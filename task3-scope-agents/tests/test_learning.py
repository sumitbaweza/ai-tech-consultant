"""The learning loop: hour edits -> rules -> playbook versions, and the gap that measures it."""

from typing import Any

from app.agents import learner
from app.api.routes.playbook import hours_gaps
from app.handoffs import EstimationRule, Hours, Playbook
from tests.fakes import FakeClaudeAPI, make_claude
from tests.test_scope import v1_scope

RULE = {"text": "Blog setup takes 2 likely hours, not 5", "role": "developer", "evidence": "T2"}


def edited_scope() -> Any:
    """v1 with T2's hours cut by a human, and a QA task added by hand."""
    scope = v1_scope()
    scope.tasks[1] = scope.tasks[1].model_copy(update={"low": 1, "likely": 2, "high": 3})
    added = scope.tasks[2].model_copy(update={"id": "T4", "origin": "human", "ai_hours": None})
    scope.tasks.append(added)
    return scope


def test_hour_edits_find_changed_added_and_removed_tasks() -> None:
    edits = learner.hour_edits(edited_scope(), removed_titles=["Write a style guide"])

    assert [(e.kind, e.task) for e in edits] == [
        ("changed", "Task T2"),
        ("added", "Task T3"),
        ("removed", "Write a style guide"),
    ]
    assert edits[0].ai == Hours(low=3, likely=5, high=10)
    assert edits[0].approved == Hours(low=1, likely=2, high=3)


async def test_no_edits_means_no_call_and_nothing_learned() -> None:
    api = FakeClaudeAPI({"learn_estimation_rules": {"rules": [RULE]}})

    learned = await learner.learn(make_claude(api), None, v1_scope(), [])

    assert learned is None
    assert api.requests == []


async def test_edits_become_rules_capped_at_three_new_ones() -> None:
    many = [{**RULE, "text": f"Rule {n}"} for n in range(6)]
    api = FakeClaudeAPI({"learn_estimation_rules": {"rules": many}})
    current = Playbook(version=1, rules=[EstimationRule(text="Old", role=None, evidence="x")])

    learned = await learner.learn(make_claude(api), current, edited_scope(), [])

    assert learned is not None and len(learned) == 1 + learner.MAX_NEW_RULES
    sent = api.requests[0][1]["messages"][0]["content"]
    assert '"kind": "changed"' in sent and "<playbook>" in sent


async def test_unchanged_rules_make_no_new_version() -> None:
    current = Playbook(version=1, rules=[EstimationRule.model_validate(RULE)])
    api = FakeClaudeAPI({"learn_estimation_rules": {"rules": [RULE]}})

    assert await learner.learn(make_claude(api), current, edited_scope(), []) is None


def test_the_hours_gap_is_measured_per_playbook_version() -> None:
    before = edited_scope()  # T2: AI 5.5 expected, approved 2.0
    after = v1_scope()
    after.playbook_version = 1  # every task approved as the AI estimated it

    [none, v1] = hours_gaps([before, after])

    assert (none.playbook_version, none.scopes) == (None, 1)
    assert none.ai_hours == round(4.3 + 5.5 + 0.9, 1)
    assert none.approved_hours == round(4.3 + 2.0 + 0.9, 1)
    assert none.gap_percent == round(100 * 3.5 / 7.2, 1)
    assert (v1.playbook_version, v1.gap_percent) == (1, 0.0)
