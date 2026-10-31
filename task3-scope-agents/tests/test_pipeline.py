"""The whole team through the API, with a fake Claude and a fake Brain (needs Postgres).

First meeting -> questions -> answers -> estimate sent back once by the reviewer -> draft v1
-> export refused -> human edit -> approval -> export -> follow-up meeting -> v2 with a
change order -> approval, with v1 kept as superseded.
"""

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_orchestrator, wait_for_runs
from app.api.main import app
from app.config import get_settings
from app.db import get_session
from app.orchestrator import Orchestrator, Team
from tests.fakes import (
    BRAIN_URL,
    FakeClaudeAPI,
    ask_reply,
    brain_routes,
    hit,
    make_brain,
    make_claude,
    search_reply,
)

pytestmark = pytest.mark.db

HEADERS = {"X-API-Key": "test-key-1"}

MEETING_1 = """[00:00:05] Priya: We need new members to be approved before their listing goes live.
[00:00:20] Sumit: Got it.
[00:00:25] Priya: Honestly the search needs to be much better than it is today.
[00:00:40] Priya: Please add a blog so we can post articles every week.
"""

MEETING_2 = """[00:00:05] Priya: For search we only need filters for city, specialty and insurance.
[00:00:20] Priya: Patients should book appointments online from each dentist profile.
[00:00:30] Priya: We are dropping the blog, nobody will write for it.
"""


def extract(text: str) -> dict[str, Any]:
    """Every line Priya says becomes one requirement, quoted exactly."""
    said = re.findall(r"\] Priya: (.+)", text)
    return {
        "requirements": [
            {
                "title": line[:30],
                "detail": line,
                "quote": line,
                "priority": "must",
                "is_vague": "better" in line,
            }
            for line in said
        ]
    }


def merge(text: str) -> dict[str, Any]:
    """One group per item: the merger found no duplicates."""
    return {
        "requirements": [
            {
                "title": f"Item {iid}",
                "detail": iid,
                "priority": "must",
                "is_vague": False,
                "source_ids": [iid],
                "conflict": None,
            }
            for iid in re.findall(r'"id": "(C\d+-\d+)"', text)
        ]
    }


def fit(text: str) -> dict[str, Any]:
    """Member approval is a setting the help center shows; the rest is custom work."""
    if "approved" in text:
        return {
            "kind": "setting",
            "reason": "Built in.",
            "setting_path": "Members > Approval",
            "evidence_chunk_ids": [11],
        }
    return {
        "kind": "custom",
        "reason": "Not built in.",
        "setting_path": None,
        "evidence_chunk_ids": [],
    }


def estimate(*rids: str) -> dict[str, Any]:
    return {
        "tasks": [
            {
                "requirement_ids": [rid],
                "title": f"Do {rid}",
                "description": "Build it.",
                "role": "developer",
                "low": 1,
                "likely": 2,
                "high": 4,
                "basis": "Similar work.",
            }
            for rid in rids
        ],
        "assumptions": ["The client supplies content."],
        "exclusions": ["Hosting."],
    }


PROPOSAL = {
    "title": "Proposal: Bright Smile",
    "summary": "We'll build it.",
    "understanding": "U.",
    "approach": "A.",
    "settings_note": "S.",
    "timeline": "T.",
    "next_steps": "N.",
}

CHANGES = {
    "changes": [
        {
            "label": "changed",
            "followup_id": "M1",
            "v1_requirement_id": "R2",
            "title": "Search filters",
            "before": "Better search",
            "after": "City, specialty, insurance filters",
            "in_signed_scope": False,
            "extra_low": 1,
            "extra_likely": 2,
            "extra_high": 3,
            "role": "developer",
            "reason": "Insurance is new.",
            "quote": None,
        },
        {
            "label": "new",
            "followup_id": "M2",
            "v1_requirement_id": None,
            "title": "Online booking",
            "before": None,
            "after": "Book from the profile",
            "in_signed_scope": False,
            "extra_low": 10,
            "extra_likely": 16,
            "extra_high": 30,
            "role": "developer",
            "reason": "Not in v1.",
            "quote": None,
        },
        {
            "label": "removed",
            "followup_id": "M3",
            "v1_requirement_id": "R3",
            "title": "Blog",
            "before": "A weekly blog",
            "after": None,
            "in_signed_scope": True,
            "extra_low": 0,
            "extra_likely": 0,
            "extra_high": 0,
            "role": "developer",
            "reason": "Dropped.",
            "quote": None,
        },
    ]
}


def scripted_claude() -> FakeClaudeAPI:
    return FakeClaudeAPI(
        {
            "extract_requirements": extract,
            "merge_requirements": merge,
            "check_fit": fit,
            "clarify_requirements": {
                "questions": [
                    {
                        "requirement_id": "R2",
                        "question": "Which filters?",
                        "why_it_matters": "Each filter is work.",
                        "default_assumption": "City and specialty only.",
                    },
                    {
                        "requirement_id": None,
                        "question": "Who writes the posts?",
                        "why_it_matters": "Content.",
                        "default_assumption": "The client does.",
                    },
                ]
            },
            # The first estimate forgets the blog (R3); the reviewer's code check catches it
            "estimate_tasks": [estimate("R1", "R2"), estimate("R1", "R2", "R3")],
            "review_scope": {"verdict": "approve", "summary": "Fine.", "issues": []},
            "write_proposal": PROPOSAL,
            "analyze_changes": CHANGES,
        }
    )


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AsyncClient, FakeClaudeAPI]]:
    claude = scripted_claude()
    team = Team(make_claude(claude), make_brain(), get_settings(), session_factory)

    async def session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(team)
    app.dependency_overrides[get_session] = session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as http:
        yield http, claude
    app.dependency_overrides.clear()


async def call(http: AsyncClient, method: str, path: str, expected: int, **body: Any) -> Any:
    response = await http.request(method, path, json=body or None)
    assert response.status_code == expected, response.text
    await wait_for_runs()  # let a run started by this request finish
    return response.json() if "json" in response.headers.get("content-type", "") else response.text


@pytest.mark.respx(assert_all_called=False)
async def test_first_meeting_to_approved_v1_then_a_followup_to_v2(
    client: tuple[AsyncClient, FakeClaudeAPI], respx_mock: respx.MockRouter
) -> None:
    http, claude = client
    brain_routes(
        respx_mock,
        search_reply(hit(11, "Member approval", "Require approval for new members")),
        ask_reply("Members > Settings > Require Approval."),
    )

    project = await call(http, "POST", "/v1/projects", 201, name="Directory", client="Bright Smile")
    run = await call(
        http,
        "POST",
        f"/v1/projects/{project['id']}/meetings",
        202,
        title="Meeting 1",
        transcript=MEETING_1,
    )
    run = await call(http, "GET", f"/v1/runs/{run['id']}", 200)

    # Steps 1-5 ran, and the run waits for a human to answer the consultant
    assert run["kind"] == "first"
    assert run["status"] == "awaiting_answers", run["error"]
    assert [s["agent"] for s in run["steps"]] == [
        "splitter",
        "extractor",
        "merger",
        "fit_checker",
        "consultant",
    ]
    fits = run["steps"][3]["output"]["results"]
    assert [f["kind"] for f in fits] == ["setting", "custom", "custom"]
    assert fits[0]["how_to"] == "Members > Settings > Require Approval."

    # One answer given, one left blank: the blank one becomes an assumption
    await call(
        http,
        "POST",
        f"/v1/runs/{run['id']}/answers",
        202,
        by="Sumit",
        answers=[
            {"question_id": "Q1", "answer": "City, specialty, insurance"},
            {"question_id": "Q2", "answer": ""},
        ],
    )
    run = await call(http, "GET", f"/v1/runs/{run['id']}", 200)
    assert run["status"] == "awaiting_approval", run["error"]
    agents = [s["agent"] for s in run["steps"]]
    # The reviewer sent the first estimate back once (R3 had no task), then it passed
    assert agents[5:] == [
        "human_answers",
        "estimator",
        "reviewer",
        "estimator",
        "reviewer",
        "proposal_writer",
    ]
    assert "senior reviewer sent it back" in str(claude.calls("estimate_tasks")[1]["messages"])

    version = await call(http, "GET", f"/v1/versions/{run['version']['id']}", 200)
    assert (version["version"], version["status"]) == (1, "draft")
    assert "The client does." in version["scope"]["assumptions"]
    assert version["proposal_markdown"].startswith("> **DRAFT")

    # Nothing leaves before approval
    await call(http, "GET", f"/v1/versions/{version['id']}/proposal.md", 409)

    # A human edits one task's hours, then approves
    tasks = [
        {**{k: t[k] for k in ("id", "requirement_ids", "title", "role", "low", "likely", "high")}}
        for t in version["scope"]["tasks"]
    ]
    tasks[1].update(low=3, likely=6, high=9)
    edited = await call(
        http,
        "PUT",
        f"/v1/versions/{version['id']}/scope",
        200,
        by="Sumit",
        tasks=tasks,
        assumptions=version["scope"]["assumptions"],
        exclusions=[],
    )
    assert edited["scope"]["tasks"][1]["likely"] == 6
    assert edited["scope"]["tasks"][1]["ai_hours"]["likely"] == 2
    approved = await call(
        http, "POST", f"/v1/versions/{version['id']}/approve", 200, by="Sumit", note="Good."
    )
    assert (approved["status"], approved["decided_by"]) == ("approved", "Sumit")
    markdown = await call(http, "GET", f"/v1/versions/{version['id']}/proposal.md", 200)
    assert "DRAFT" not in markdown and "## Estimate" in markdown

    # An approved version is frozen
    await call(
        http,
        "PUT",
        f"/v1/versions/{version['id']}/scope",
        409,
        by="Sumit",
        tasks=tasks,
        assumptions=[],
        exclusions=[],
    )

    # The next meeting is a follow-up, compared against approved v1
    run2 = await call(
        http,
        "POST",
        f"/v1/projects/{project['id']}/meetings",
        202,
        title="Meeting 2",
        transcript=MEETING_2,
    )
    run2 = await call(http, "GET", f"/v1/runs/{run2['id']}", 200)
    assert run2["kind"] == "followup"
    assert run2["status"] == "awaiting_approval", run2["error"]
    assert [s["agent"] for s in run2["steps"]] == [
        "splitter",
        "extractor",
        "merger",
        "change_analyst",
        "proposal_writer",
    ]
    v2 = await call(http, "GET", f"/v1/versions/{run2['version']['id']}", 200)
    assert v2["version"] == 2
    labels = [(c["label"], c["v2_requirement_id"]) for c in v2["scope"]["changes"]]
    assert labels == [("changed", "R2"), ("new", "R4"), ("removed", "R3")]
    # The change analyst read the follow-up transcript itself, not only the extracted list
    [analysis] = claude.calls("analyze_changes")
    assert "dropping the blog, nobody will write for it" in analysis["messages"][0]["content"]
    assert v2["scope"]["totals"]["change_order_hours"] == round(2.0 + 17.3, 1)
    assert "## Changes since version 1" in v2["proposal_markdown"]

    await call(http, "POST", f"/v1/versions/{v2['id']}/approve", 200, by="Sumit")
    project = await call(http, "GET", f"/v1/projects/{project['id']}", 200)
    assert [(v["version"], v["status"]) for v in project["versions"]] == [
        (1, "superseded"),
        (2, "approved"),
    ]


@pytest.mark.respx(assert_all_called=False)
async def test_send_back_redoes_the_estimate_with_the_humans_note(
    client: tuple[AsyncClient, FakeClaudeAPI], respx_mock: respx.MockRouter
) -> None:
    http, claude = client
    claude.replies["clarify_requirements"] = {"questions": []}  # no questions: no pause
    claude.replies["estimate_tasks"] = estimate("R1", "R2", "R3")
    brain_routes(respx_mock, search_reply(), ask_reply("x"))  # an empty Brain

    project = await call(http, "POST", "/v1/projects", 201, name="P", client="C")
    run = await call(
        http, "POST", f"/v1/projects/{project['id']}/meetings", 202, title="M", transcript=MEETING_1
    )
    run = await call(http, "GET", f"/v1/runs/{run['id']}", 200)
    assert run["status"] == "awaiting_approval", run["error"]
    fits = next(s for s in run["steps"] if s["agent"] == "fit_checker")["output"]["results"]
    assert {f["brain"] for f in fits} == {"no_match"}

    # A second meeting can't start while this one waits for approval
    await call(
        http, "POST", f"/v1/projects/{project['id']}/meetings", 409, title="M2", transcript="A: b"
    )

    sent = await call(
        http,
        "POST",
        f"/v1/versions/{run['version']['id']}/send-back",
        202,
        by="Sumit",
        note="The blog needs a designer.",
    )
    run = await call(http, "GET", f"/v1/runs/{sent['id']}", 200)
    assert run["status"] == "awaiting_approval"
    assert "The blog needs a designer." in str(claude.calls("estimate_tasks")[-1]["messages"])
    assert run["version"]["version"] == 1  # the same draft, revised


async def test_a_bad_model_reply_fails_the_run_with_the_reason_and_retry_resumes(
    client: tuple[AsyncClient, FakeClaudeAPI], respx_mock: respx.MockRouter
) -> None:
    http, claude = client
    claude.replies["merge_requirements"] = {"requirements": "not a list"}  # a broken reply
    claude.replies["clarify_requirements"] = {"questions": []}
    claude.replies["estimate_tasks"] = estimate("R1", "R2", "R3")
    # The Brain is down too: the fit checker carries on without it
    respx_mock.post(f"{BRAIN_URL}/v1/search").mock(side_effect=httpx.ConnectError("down"))

    project = await call(http, "POST", "/v1/projects", 201, name="P", client="C")
    run = await call(
        http,
        "POST",
        f"/v1/projects/{project['id']}/meetings",
        202,
        title="M",
        transcript=MEETING_1,
    )
    run = await call(http, "GET", f"/v1/runs/{run['id']}", 200)
    assert run["status"] == "failed"
    assert "LLMBadOutput" in run["error"]
    assert (run["steps"][-1]["agent"], run["steps"][-1]["status"]) == ("merger", "failed")

    claude.replies["merge_requirements"] = merge  # the model behaves on the second try
    await call(http, "POST", f"/v1/runs/{run['id']}/retry", 202)
    run = await call(http, "GET", f"/v1/runs/{run['id']}", 200)
    assert run["status"] == "awaiting_approval", run["error"]
    fits = next(s for s in run["steps"] if s["agent"] == "fit_checker")["output"]["results"]
    assert {(f["kind"], f["brain"]) for f in fits} == {("unverified", "unavailable")}


async def test_the_api_needs_a_key() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/v1/projects")).status_code == 401
        assert (await http.get("/health")).status_code == 200
