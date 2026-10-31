"""The capability check against a fake Claude API, on test_search's small help center."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_claude
from app.api.main import app
from app.db import get_session
from app.llm import load_prompt
from app.rag.capability import FAILED, NOT_COVERED, capability_content, check_capability
from app.rag.embed import get_voyage
from tests.fakes import make_claude, message_json
from tests.test_answer import OVERLOADED, FakeEmbedder
from tests.test_search import SITE, help_center

LABELS = f"{SITE}/1-text-labels"


class FakeClaude:
    """Answers the check_capability prompt with `draft`, and keeps what each request sent."""

    def __init__(self, draft: dict[str, Any] | None = None) -> None:
        self.draft = draft
        self.inputs: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.inputs.append(body["messages"][0]["content"][0]["text"])
        if self.draft is None:
            return httpx2.Response(529, json=OVERLOADED)
        return httpx2.Response(200, json=message_json(json.dumps(self.draft)))


def verdict(name: str, citations: list[str], setting: str | None = None) -> dict[str, Any]:
    return {
        "verdict": name,
        "setting": setting,
        "explanation": "Text Labels renames any word on the site.",
        "citations": citations,
        "confidence": 0.9,
    }


def test_the_prompt_and_the_requirement_in_tags() -> None:
    prompt = load_prompt("check_capability", 1)
    [block] = capability_content("Rename 'Members' to 'Pros'", "<sources>\n</sources>")

    assert (prompt.model, prompt.fallback_model) == ("claude-sonnet-5-5", "claude-haiku-5-5")
    assert block == {
        "type": "text",
        "text": "<sources>\n</sources>\n\n<requirement>Rename 'Members' to 'Pros'</requirement>",
    }


@pytest.mark.db
async def test_a_covered_requirement_names_the_setting(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(verdict("yes", ["S1", "S9"], setting="Settings » Text Labels"))

    result = await check_capability(
        db_session, make_claude(api), FakeEmbedder(), "Rename the word Members"
    )

    assert (result.verdict, result.setting, result.outcome) == (
        "yes",
        "Settings » Text Labels",
        "checked",
    )
    assert [citation.source_id for citation in result.citations] == ["S1"]  # S9 isn't real
    assert result.citations[0].url == LABELS
    assert "<requirement>Rename the word Members</requirement>" in api.inputs[0]


@pytest.mark.db
async def test_a_no_keeps_its_citation_but_no_setting(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(verdict("no", ["S1"], setting="Settings » Text Labels"))

    result = await check_capability(db_session, make_claude(api), FakeEmbedder(), "Rename it all")

    assert (result.verdict, result.setting, len(result.citations)) == ("no", None, 1)


@pytest.mark.db
async def test_a_verdict_citing_no_real_source_becomes_unknown(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(verdict("yes", ["S9"], setting="Magic"))

    result = await check_capability(db_session, make_claude(api), FakeEmbedder(), "Rename words")

    assert (result.verdict, result.outcome, result.explanation) == (
        "unknown",
        "no_valid_citation",
        NOT_COVERED,
    )


@pytest.mark.db
async def test_nothing_close_is_unknown_without_calling_claude(db_session: AsyncSession) -> None:
    await help_center(db_session)
    api = FakeClaude(verdict("yes", ["S1"]))

    # Direction 5 is far from every chunk
    result = await check_capability(db_session, make_claude(api), FakeEmbedder(5), "Bake bread")

    assert (result.verdict, result.outcome) == ("unknown", "low_retrieval")
    assert api.inputs == []


@pytest.mark.db
async def test_a_model_outage_is_reported_as_unknown(db_session: AsyncSession) -> None:
    await help_center(db_session)

    result = await check_capability(
        db_session, make_claude(FakeClaude()), FakeEmbedder(), "Rename words"
    )

    assert (result.verdict, result.outcome, result.explanation) == (
        "unknown",
        "model_failed",
        FAILED,
    )


@pytest.mark.db
async def test_the_capability_endpoint(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await help_center(session)

    async def test_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    claude = make_claude(FakeClaude(verdict("partly", ["S1"], setting="Settings » Text Labels")))
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_voyage] = FakeEmbedder
    app.dependency_overrides[require_claude] = lambda: claude
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key-1"},
        ) as api:
            checked = await api.post(
                "/v1/capabilities/check", json={"requirement": "Rename the word Members"}
            )
            empty = await api.post("/v1/capabilities/check", json={"requirement": ""})
    finally:
        app.dependency_overrides.clear()

    assert checked.status_code == 200
    assert (checked.json()["verdict"], checked.json()["citations"][0]["url"]) == ("partly", LABELS)
    assert empty.status_code == 422
