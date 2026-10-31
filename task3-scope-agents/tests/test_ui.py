"""The screens, against a fake scope API, run by Streamlit's own test harness."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
import respx
from streamlit.testing.v1 import AppTest

from app.config import get_settings
from tests.test_scope import v1_scope

API = get_settings().scope_api_url

QUESTIONS = {
    "questions": [
        {
            "id": "Q1",
            "requirement_id": "R2",
            "question": "Which search filters do patients need?",
            "why_it_matters": "Each filter is work.",
            "default_assumption": "City and specialty only.",
            "answer": None,
        }
    ]
}
RUN: dict[str, Any] = {
    "id": 7,
    "kind": "first",
    "status": "awaiting_answers",
    "meeting_id": 3,
    "meeting_title": "Meeting 1",
    "created_at": "2026-10-31T10:00:00Z",
    "project_id": 1,
    "error": None,
    "updated_at": "2026-10-31T10:01:00Z",
    "steps": [
        {
            "id": 5,
            "agent": "consultant",
            "status": "done",
            "output": QUESTIONS,
            "error": None,
            "started_at": "2026-10-31T10:00:00Z",
            "finished_at": "2026-10-31T10:00:01Z",
        }
    ],
    "version": None,
    "costs": {"consultant": "0.0123"},
    "total_cost": "0.0123",
}
SUMMARY = {
    "id": 4,
    "project_id": 1,
    "run_id": 7,
    "version": 1,
    "status": "draft",
    "expected_hours": 10.7,
    "price": 535.0,
    "change_order_price": 0.0,
    "decided_by": None,
    "decided_at": None,
}
PROJECT = {
    "id": 1,
    "name": "Directory",
    "client": "Bright Smile",
    "created_at": "2026-10-31T10:00:00Z",
    "runs": [
        {k: RUN[k] for k in ("id", "kind", "status", "meeting_id", "meeting_title", "created_at")}
    ],
    "versions": [SUMMARY],
}
VERSION = {
    **SUMMARY,
    "scope": v1_scope().model_dump(mode="json"),
    "note": None,
    "proposal_markdown": "> **DRAFT: not approved.**\n\n# Proposal",
}


@pytest.fixture
def api() -> Iterator[respx.Router]:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.get("/v1/projects").respond(json=[PROJECT])
        router.get("/v1/runs/7").respond(json=RUN)
        router.get("/v1/versions/4").respond(json=VERSION)
        yield router


def run_screen(name: str) -> None:
    """Runs as the app script: one screen, the way ui/main.py runs it."""
    import importlib

    importlib.import_module(f"ui.screens.{name}").render()


def screen(name: str, **state: Any) -> AppTest:
    app = AppTest.from_function(run_screen, kwargs={"name": name}, default_timeout=10)
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def test_new_meeting_offers_the_sample_transcripts(api: respx.Router) -> None:
    app = screen("meeting")

    assert not app.exception
    samples = app.selectbox[1].options  # [0] picks the project
    assert "01-first-meeting-bright-smile.txt" in samples
    assert "03-followup-bright-smile.txt" in samples


def test_the_consultants_questions_are_answered_as_the_named_reviewer(api: respx.Router) -> None:
    answered = api.post("/v1/runs/7/answers").respond(json={**RUN, "status": "running"})
    app = screen("run", run_id=7, reviewer="Sumit")
    assert not app.exception
    [field] = app.text_area

    field.input("City, specialty and insurance")
    app.button[0].click().run()

    body = json.loads(answered.calls.last.request.content)
    assert body == {
        "by": "Sumit",
        "answers": [{"question_id": "Q1", "answer": "City, specialty and insurance"}],
    }


def test_a_draft_is_approved_from_the_decision_tab(api: respx.Router) -> None:
    approved = api.post("/v1/versions/4/approve").respond(
        json={**VERSION, "status": "approved", "decided_by": "Sumit"}
    )
    app = screen("scope", version_id=4, reviewer="Sumit")
    assert not app.exception
    assert "approves the version" in app.warning[0].value  # a draft can't be exported

    [approve] = [b for b in app.button if b.label == "Approve"]
    approve.click().run()

    assert json.loads(approved.calls.last.request.content) == {"by": "Sumit", "note": None}
