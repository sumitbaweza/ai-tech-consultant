"""The expert queue: unanswered questions become tasks, a repeat counts on its task, a thumbs
down flags a wrong answer, and a dismissed question stays out of the queue.

The database tests ask through the API, against the small help center from test_search, a
fake Claude and an embedder that points every question one way.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_claude
from app.api.main import app
from app.db import get_session
from app.experts import question_key
from app.rag.embed import get_voyage
from tests.fakes import make_claude
from tests.test_answer import FakeClaude, FakeEmbedder, draft
from tests.test_search import help_center

WEATHER = "What's the weather in Berlin?"
NOTHING_CLOSE = 5  # the embedder direction no chunk points in


def test_a_question_key_ignores_case_spaces_and_punctuation() -> None:
    assert question_key("How do I add a  LOGO?") == "how do i add a logo"
    assert question_key(WEATHER) == question_key("what's the WEATHER in berlin")


@pytest.fixture
async def api(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    async with session_factory() as session:
        await help_center(session)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def brain(claude_api: FakeClaude, direction: int) -> None:
    """Point /v1/ask at a fake Claude, and an embedder that turns every question one way."""
    app.dependency_overrides[require_claude] = lambda: make_claude(claude_api)
    app.dependency_overrides[get_voyage] = lambda: FakeEmbedder(direction)


@pytest.mark.db
async def test_an_unanswered_question_opens_one_task_and_repeats_count_on_it(
    api: httpx.AsyncClient,
) -> None:
    brain(FakeClaude(), NOTHING_CLOSE)

    first = (await api.post("/v1/ask", json={"question": WEATHER})).json()
    again = (await api.post("/v1/ask", json={"question": "what's the WEATHER in berlin"})).json()
    other = (await api.post("/v1/ask", json={"question": "Who won the match?"})).json()
    queue = (await api.get("/v1/expert-tasks")).json()

    assert first["outcome"] == "low_retrieval"
    assert first["expert_task_id"] is not None
    assert again["expert_task_id"] == first["expert_task_id"]
    assert queue["total"] == 2
    top, second = queue["items"]  # the most asked first
    assert (top["question"], top["asked_count"], top["reason"]) == (WEATHER, 2, "low_retrieval")
    assert len(top["retrieved"]) == len(first["sources"]) == 3  # what search found
    assert second["id"] == other["expert_task_id"]


@pytest.mark.db
async def test_a_follow_up_keeps_the_chat_in_the_users_words(api: httpx.AsyncClient) -> None:
    standalone = "What's the weather in Berlin for a Text Labels launch?"
    brain(FakeClaude(standalone=standalone), NOTHING_CLOSE)
    history = [
        {"role": "user", "content": "How do I rename words?"},
        {"role": "assistant", "content": "Use Text Labels."},
    ]

    await api.post("/v1/ask", json={"question": "and the weather?", "history": history})

    [task] = (await api.get("/v1/expert-tasks")).json()["items"]
    assert task["question"] == standalone
    assert task["history"] == [*history, {"role": "user", "content": "and the weather?"}]


@pytest.mark.db
async def test_a_model_outage_or_an_answer_opens_no_task(api: httpx.AsyncClient) -> None:
    brain(FakeClaude(down=True), 0)
    failed = (await api.post("/v1/ask", json={"question": "How do I rename words?"})).json()
    brain(FakeClaude(draft("Open Settings » Text Labels.", ["S1"])), 0)
    answered = (await api.post("/v1/ask", json={"question": "How do I rename words?"})).json()

    assert (failed["outcome"], failed["expert_task_id"]) == ("model_failed", None)
    assert (answered["outcome"], answered["expert_task_id"]) == ("answered", None)
    assert (await api.get("/v1/expert-tasks")).json()["total"] == 0


@pytest.mark.db
async def test_a_thumbs_down_flags_the_wrong_answer(api: httpx.AsyncClient) -> None:
    flag = {"question": "How do I rename words?", "answer": "Edit the theme files."}

    flagged = await api.post("/v1/expert-tasks", json=flag)
    again = (await api.post("/v1/expert-tasks", json=flag)).json()

    assert flagged.status_code == 201
    assert (again["id"], again["reason"], again["asked_count"]) == (
        flagged.json()["id"],
        "thumbs_down",
        2,
    )
    assert again["given_answer"] == "Edit the theme files."


@pytest.mark.db
async def test_a_dismissed_question_stays_out_of_the_queue(api: httpx.AsyncClient) -> None:
    brain(FakeClaude(), NOTHING_CLOSE)
    task_id = (await api.post("/v1/ask", json={"question": WEATHER})).json()["expert_task_id"]

    dismissed = (
        await api.post(f"/v1/expert-tasks/{task_id}/dismiss", json={"expert": "Sam"})
    ).json()
    asked_again = (await api.post("/v1/ask", json={"question": WEATHER})).json()

    assert (dismissed["status"], dismissed["answered_by"]) == ("dismissed", "Sam")
    assert asked_again["expert_task_id"] == task_id  # counted on the dismissed task
    assert (await api.get("/v1/expert-tasks")).json()["total"] == 0
    [task] = (await api.get("/v1/expert-tasks", params={"status": "dismissed"})).json()["items"]
    assert task["asked_count"] == 2
    dismiss_again = await api.post(f"/v1/expert-tasks/{task_id}/dismiss", json={"expert": "Sam"})
    assert dismiss_again.status_code == 409
    unknown = await api.post("/v1/expert-tasks/9999/dismiss", json={"expert": "Sam"})
    assert unknown.status_code == 404
