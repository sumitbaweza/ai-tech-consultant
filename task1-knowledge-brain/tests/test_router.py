"""The router: which model answers, and when Sonnet takes over from Haiku."""

import json
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChunkKind, PageKind
from app.rag.answer import Outcome, ask
from app.rag.router import HIGH_SIMILARITY, Tier, choose_route, is_single_fact
from app.rag.search import SearchHit
from tests.fakes import make_claude, message_json
from tests.test_answer import FakeEmbedder, draft
from tests.test_search import help_center


def hit(similarity: float | None) -> SearchHit:
    return SearchHit(
        chunk_id=1,
        page_id=1,
        page_kind=PageKind.ARTICLE,
        kind=ChunkKind.TEXT,
        title="Text Labels",
        url="https://help.example/1",
        image_url=None,
        content="Settings > Text Labels",
        score=0.03,
        similarity=similarity,
    )


class ModelClaude:
    """Answers each model with its own draft, and records which models were asked."""

    def __init__(self, haiku: dict[str, Any] | None, sonnet: dict[str, Any]) -> None:
        self.drafts = {"claude-haiku-5-5": haiku, "claude-sonnet-5-5": sonnet}
        self.models: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        model = json.loads(request.content)["model"]
        self.models.append(model)
        reply = self.drafts[model]
        if reply is None:  # this model is down
            error = {"type": "error", "error": {"type": "overloaded_error", "message": "Busy"}}
            return httpx2.Response(529, json=error)
        return httpx2.Response(200, json=message_json(json.dumps(reply), model=model))


# --- The rules ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "How do I manually approve new member signups?",
        "Where do I change the site logo?",
        "What is the maximum upload size",
    ],
)
def test_single_fact_questions(question: str) -> None:
    assert is_single_fact(question)


@pytest.mark.parametrize(
    "question",
    [
        "How do I set up upgrades and change emails?",  # two things
        "What's the difference between Stripe and PayPal?",  # a comparison
        "Why is my sitemap not updating?",  # a reason
        "Where is the logo? And can I resize it?",  # two questions
        "I changed the logo. Where do I see it?",  # two sentences
        "How do I " + "really " * 20 + "change the logo?",  # too long
    ],
)
def test_questions_that_need_more_than_one_fact(question: str) -> None:
    assert not is_single_fact(question)


def test_an_easy_question_with_a_close_match_goes_to_haiku() -> None:
    route = choose_route("Where do I change the site logo?", [hit(0.66), hit(0.5)])

    assert (route.tier, route.prompt, route.top_similarity) == (Tier.HAIKU, "answer_fast", 0.66)
    assert "close match (0.66)" in route.reason


def test_everything_else_goes_to_sonnet() -> None:
    weak = choose_route("Where do I change the site logo?", [hit(HIGH_SIMILARITY - 0.01)])
    hard = choose_route("Why is my logo blurry?", [hit(0.66)])
    words_only = choose_route("Where do I change the site logo?", [hit(None)])

    assert [route.tier for route in (weak, hard, words_only)] == [Tier.SONNET] * 3
    assert "under 0.60" in weak.reason
    assert "more than one fact" in hard.reason
    assert "exact-word search" in words_only.reason


def test_the_caller_can_force_a_model() -> None:
    route = choose_route("Why is my logo blurry?", [hit(0.3)], mode="haiku")

    assert (route.tier, route.mode, route.reason) == (
        Tier.HAIKU,
        "haiku",
        "forced to haiku by the caller",
    )


# --- Escalation, through ask() --------------------------------------------------------------
# FakeEmbedder points every question at the Text Labels article, a close match, so an easy
# question goes to Haiku first


@pytest.mark.db
async def test_a_good_haiku_answer_is_kept(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = ModelClaude(haiku=draft("Open Settings » Text Labels.", ["S1"]), sonnet=draft("x", []))

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [])

    assert result.outcome == Outcome.ANSWERED
    assert api.models == ["claude-haiku-5-5"]
    assert result.route is not None
    assert (result.route.tier, result.route.escalated) == (Tier.HAIKU, False)


@pytest.mark.parametrize(
    ("haiku", "why"),
    [
        (draft("Not covered.", [], answered=False), "found no answer"),
        (draft("Click the magic button.", ["S9"]), "cited none"),
        ({**draft("Maybe Settings.", ["S1"]), "confidence": 0.4}, "confidence was 0.40"),
        (None, "no usable answer"),  # Haiku is down
    ],
)
@pytest.mark.db
async def test_a_weak_haiku_answer_goes_to_sonnet(
    db_session: AsyncSession, haiku: dict[str, Any] | None, why: str
) -> None:
    await help_center(db_session)
    api = ModelClaude(haiku=haiku, sonnet=draft("Open Settings » Text Labels.", ["S1"]))

    result = await ask(db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [])

    assert (result.outcome, result.answer) == (Outcome.ANSWERED, "Open Settings » Text Labels.")
    assert api.models == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert result.route is not None
    assert (result.route.tier, result.route.escalated) == (Tier.SONNET, True)
    assert why in result.route.reason


@pytest.mark.db
async def test_a_forced_model_is_never_escalated(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = ModelClaude(haiku=draft("Not covered.", [], answered=False), sonnet=draft("x", []))

    result = await ask(
        db_session, make_claude(api), FakeEmbedder(), "How do I rename words?", [], mode="haiku"
    )

    assert result.outcome == Outcome.NOT_IN_SOURCES
    assert api.models == ["claude-haiku-5-5"]
