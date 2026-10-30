"""Answering with citations, or an honest "I don't know", against a fake Claude API.

The search data is the small help center from test_search: a Text Labels article with a
screenshot, an Email Forwarders article, and an archived copy of Text Labels.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_claude
from app.api.main import app
from app.db import get_session
from app.llm import load_prompt
from app.models import ChunkKind, Page
from app.rag.answer import (
    DONT_KNOW,
    FAILED,
    ChatTurn,
    Outcome,
    answer_content,
    ask,
    rewrite_content,
)
from app.rag.embed import InputType, get_voyage
from app.rag.search import SearchHit
from tests.fakes import make_claude, message_json
from tests.test_search import SITE, help_center, unit

OVERLOADED = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}


class FakeClaude:
    """Answers the rewrite prompt with `standalone` and the answer prompt with `draft`, and
    records which prompt each request ran and what it was sent."""

    def __init__(
        self, draft: dict[str, Any] | None = None, standalone: str = "", down: bool = False
    ) -> None:
        self.draft = draft
        self.standalone = standalone
        self.down = down
        self.prompts: list[str] = []
        self.answer_inputs: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if "You rewrite the last message" in body["system"]:
            self.prompts.append("rewrite")
            reply = json.dumps({"question": self.standalone})
            return httpx2.Response(200, json=message_json(reply))
        self.prompts.append("answer")
        self.answer_inputs.append(body["messages"][0]["content"][0]["text"])
        if self.down:
            return httpx2.Response(529, json=OVERLOADED)
        return httpx2.Response(200, json=message_json(json.dumps(self.draft)))


class FakeEmbedder:
    """Points every question in one direction: 0 is the Text Labels article, 5 is nothing."""

    model = "fake-embedder"

    def __init__(self, direction: int = 0) -> None:
        self.direction = direction

    async def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        return [unit(self.direction) for _ in texts]


def draft(answer: str, citations: list[str], answered: bool = True) -> dict[str, Any]:
    return {"answer": answer, "citations": citations, "answered": answered, "confidence": 0.9}


# --- Without a database ---------------------------------------------------------------------


def test_the_prompts_and_their_models() -> None:
    answer, rewrite = load_prompt("answer", 1), load_prompt("rewrite_question", 1)

    assert (answer.model, answer.fallback_model, answer.thinking) == (
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "adaptive",
    )
    assert (rewrite.model, rewrite.thinking) == ("claude-haiku-5-5", "disabled")


def test_sources_and_the_question_go_in_tags() -> None:
    hit = SearchHit(
        chunk_id=7,
        page_id=1,
        kind=ChunkKind.IMAGE,
        title="Text Labels",
        url=f"{SITE}/1",
        image_url="https://cdn.example/1.png",
        content="The Date edited filter.",
        score=0.03,
        similarity=0.8,
    )

    [block] = answer_content("Where is the date filter?", {"S1": hit})

    assert block == {
        "type": "text",
        "text": '<sources>\n<source id="S1" kind="screenshot" title="Text Labels">\n'
        "The Date edited filter.\n</source>\n</sources>\n\n"
        "<question>Where is the date filter?</question>",
    }


def test_the_chat_goes_in_tags_for_the_rewrite() -> None:
    history = [ChatTurn(role="user", content="How do I rename words?")]

    assert rewrite_content(history, "what about buttons?") == (
        '<chat>\n<message role="user">How do I rename words?</message>\n</chat>\n\n'
        "<last_message>what about buttons?</last_message>"
    )


# --- With the test database -----------------------------------------------------------------


@pytest.mark.db
async def test_an_answer_keeps_only_citations_of_real_sources(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(draft("Open Settings » Text Labels.", ["S1", "S9", "S1"]))

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [])

    assert (result.outcome, result.answered, result.confidence) == (Outcome.ANSWERED, True, 0.9)
    [citation] = result.citations  # S9 was made up, and S1 is listed once
    assert (citation.source_id, citation.url) == ("S1", f"{SITE}/1-text-labels")
    assert citation.snippet == "Text Labels let you rename the words visitors see."
    assert '<source id="S1"' in api.answer_inputs[0]


@pytest.mark.db
async def test_sources_that_dont_answer_give_an_honest_no(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(draft("The help center doesn't cover refunds.", [], answered=False))

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "Can I get a refund?", [])

    assert (result.outcome, result.answer, result.citations) == (
        Outcome.NOT_IN_SOURCES,
        "The help center doesn't cover refunds.",
        [],
    )


@pytest.mark.db
async def test_an_answer_citing_no_real_source_becomes_dont_know(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(draft("Click the magic button.", ["S9"]))

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [])

    assert (result.outcome, result.answered, result.answer) == (
        Outcome.NO_VALID_CITATION,
        False,
        DONT_KNOW,
    )


@pytest.mark.db
async def test_a_question_far_from_everything_never_reaches_claude(
    db_session: AsyncSession,
) -> None:
    await help_center(db_session)
    api = FakeClaude(draft("unused", []))

    result = await ask(db_session, make_claude(api), FakeEmbedder(5), "What's the weather?", [])

    assert (result.outcome, result.answer) == (Outcome.LOW_RETRIEVAL, DONT_KNOW)
    assert api.prompts == []


@pytest.mark.db
async def test_a_follow_up_is_rewritten_before_searching(db_session: AsyncSession) -> None:
    await help_center(db_session)
    standalone = "How do I use the Date edited filter on Text Labels?"
    api = FakeClaude(draft("Click the Date edited field.", ["S1"]), standalone=standalone)
    history = [
        ChatTurn(role="user", content="How do I rename words on my site?"),
        ChatTurn(role="assistant", content="Use Settings » Text Labels."),
    ]

    result = await ask(
        db_session, make_claude(api), FakeEmbedder(), "and the date filter?", history
    )

    assert result.standalone_question == standalone
    assert api.prompts == ["rewrite", "answer"]
    assert f"<question>{standalone}</question>" in api.answer_inputs[0]


@pytest.mark.db
async def test_a_model_outage_is_reported_not_guessed(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(down=True)

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [])

    assert (result.outcome, result.answer) == (Outcome.MODEL_FAILED, FAILED)
    assert api.prompts == ["answer", "answer"]  # Sonnet, then its fallback model


@pytest.mark.db
async def test_the_ask_and_article_endpoints(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await help_center(session)
        labels_id = await session.scalar(select(Page.id).where(Page.url == f"{SITE}/1-text-labels"))

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    claude = make_claude(FakeClaude(draft("Open Settings » Text Labels.", ["S1"])))
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_voyage] = FakeEmbedder
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            # Without ANTHROPIC_API_KEY (the tests have none), asking is a clear 503
            missing_key = await api.post("/v1/ask", json={"question": "How do I rename words?"})
            assert missing_key.status_code == 503

            app.dependency_overrides[require_claude] = lambda: claude
            answered = await api.post("/v1/ask", json={"question": "How do I rename words?"})
            assert answered.json()["outcome"] == "answered"
            assert answered.json()["citations"][0]["page_id"] == labels_id

            article = (await api.get(f"/v1/articles/{labels_id}")).json()
            assert (article["title"], article["status"]) == ("1-text-labels", "active")
            assert article["images"] == [
                {
                    "position": 1,
                    "url": "https://cdn.example/date.png",
                    "alt": None,
                    "description": None,
                }
            ]
            assert (await api.get("/v1/articles/999999")).status_code == 404
    finally:
        app.dependency_overrides.clear()
