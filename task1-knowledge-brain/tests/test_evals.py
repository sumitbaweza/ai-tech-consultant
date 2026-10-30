"""The eval harness: the case file's rules, scoring, the check against the crawl, and cases run
through the real answer path against a fake Claude API.

The database tests use the small help center from test_search.
"""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm import DatabaseLedger
from app.models import Page, PageKind, PageStatus
from app.rag.answer import AskResult, Citation, Outcome
from evals.cases import CaseFile, EvalCase, load_cases
from evals.run import check_cases, percentile, run_case, score
from tests.fakes import make_claude
from tests.test_answer import FakeClaude, FakeEmbedder, draft
from tests.test_search import SITE, help_center

LABELS = f"{SITE}/1-text-labels"
FORWARDERS = f"{SITE}/2-email-forwarders"


def case(**fields: Any) -> EvalCase:
    return EvalCase.model_validate({"id": "a-case", "question": "How do I rename words?", **fields})


def result(
    outcome: Outcome = Outcome.ANSWERED, cited: tuple[str, ...] = (), screenshot: bool = False
) -> AskResult:
    citations = [
        Citation(
            source_id=f"S{number}",
            chunk_id=number,
            page_id=number,
            title=None,
            url=url,
            image_url="https://cdn.example/1.png" if screenshot else None,
            snippet="",
        )
        for number, url in enumerate(cited, start=1)
    ]
    return AskResult(
        question="q",
        standalone_question="q",
        answered=outcome == Outcome.ANSWERED,
        answer="a",
        citations=citations,
        confidence=0.9 if citations else 0.0,
        outcome=outcome,
        sources=[],
    )


# --- The case file ----------------------------------------------------------------------------


def test_the_shipped_case_file_is_valid() -> None:
    assert load_cases().cases


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"kind": "unanswerable", "expected_urls": [LABELS]}, "has no expected_urls"),
        ({"kind": "how_to"}, "needs expected_urls"),
        ({"kind": "follow_up", "expected_urls": [LABELS]}, "needs the history"),
        ({"kind": "archived"}, "archive_urls is for archived cases"),
        ({"kind": "how_to", "expected_urls": [LABELS], "archive_urls": [LABELS]}, "archive_urls"),
        ({"kind": "archived", "expected_urls": [LABELS], "archive_urls": [LABELS]}, "both"),
        ({"kind": "how_to", "expected_url": LABELS}, "Extra inputs are not permitted"),
        ({"kind": "how_to", "expected_urls": [LABELS], "id": "Has Spaces"}, "pattern"),
    ],
)
def test_each_kind_needs_its_own_fields(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        case(**fields)


def test_ids_are_unique() -> None:
    labels = {"id": "same", "kind": "how_to", "question": "q", "expected_urls": [LABELS]}

    with pytest.raises(ValidationError, match="used more than once: same"):
        CaseFile.model_validate({"cases": [labels, labels]})


# --- Scoring ----------------------------------------------------------------------------------


def test_a_how_to_passes_by_citing_an_expected_article() -> None:
    how_to = case(kind="how_to", expected_urls=[LABELS])

    assert score(how_to, result(cited=(FORWARDERS, LABELS))) == []
    assert score(how_to, result(cited=(FORWARDERS,))) == ["cited no expected article"]
    assert score(how_to, result(Outcome.LOW_RETRIEVAL)) == ["didn't answer (low_retrieval)"]


def test_a_screenshot_case_needs_a_screenshot_citation() -> None:
    screenshot = case(kind="screenshot", expected_urls=[LABELS])

    assert score(screenshot, result(cited=(LABELS,), screenshot=True)) == []
    assert score(screenshot, result(cited=(LABELS,))) == [
        "cited no screenshot from an expected article"
    ]


def test_an_unanswerable_case_needs_an_honest_no_not_a_failure() -> None:
    unanswerable = case(kind="unanswerable")

    assert score(unanswerable, result(Outcome.NOT_IN_SOURCES)) == []
    assert score(unanswerable, result(cited=(LABELS,))) == [
        "answered a question the help center doesn't cover"
    ]
    assert score(unanswerable, result(Outcome.MODEL_FAILED)) == [
        "Claude refused, broke the format or wasn't reachable"
    ]


def test_an_archived_article_is_never_cited() -> None:
    archived = case(kind="archived", archive_urls=[LABELS])

    assert score(archived, result(Outcome.LOW_RETRIEVAL)) == []
    assert score(archived, result(cited=(LABELS,))) == ["cited the archived article"]


def test_percentiles_by_nearest_rank() -> None:
    latencies = [100, 400, 200, 300]

    assert (percentile(latencies, 0.50), percentile(latencies, 0.95)) == (200, 400)
    assert percentile([700], 0.95) == 700


# --- With the test database -------------------------------------------------------------------


@pytest.mark.db
async def test_the_check_finds_what_would_make_a_run_meaningless(
    db_session: AsyncSession,
) -> None:
    await help_center(db_session)
    db_session.add(Page(url=f"{SITE}/4-new", kind=PageKind.ARTICLE, status=PageStatus.ACTIVE))
    db_session.add(Page(url=f"{SITE}/folder", kind=PageKind.LISTING, status=PageStatus.ACTIVE))
    await db_session.commit()
    case_file = CaseFile(
        cases=[
            case(id="fine", kind="screenshot", expected_urls=[LABELS]),
            case(id="missing", kind="how_to", expected_urls=[f"{SITE}/9-missing"]),
            case(id="old", kind="how_to", expected_urls=[f"{SITE}/3-old-labels"]),
            case(id="folder", kind="how_to", expected_urls=[f"{SITE}/folder"]),
            case(id="no-screenshots", kind="screenshot", expected_urls=[FORWARDERS]),
            case(id="new", kind="how_to", expected_urls=[f"{SITE}/4-new"]),
        ]
    )

    report = await check_cases(db_session, case_file)

    assert report.problems == [
        f"missing: never crawled: {SITE}/9-missing",
        f"old: archived, not active: {SITE}/3-old-labels",
        f"folder: a listing page, not an article: {SITE}/folder",
        f"no-screenshots: has no screenshots: {FORWARDERS}",
    ]
    assert report.not_indexed == [f"{SITE}/4-new"]  # crawled, but no chunks yet


@pytest.mark.db
async def test_a_case_runs_through_ask_and_its_cost_is_added_up(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await help_center(session)
    api = FakeClaude(draft("Open Settings » Text Labels.", ["S1"]))
    claude = make_claude(api, ledger=DatabaseLedger(session_factory))

    outcome = await run_case(
        session_factory, claude, FakeEmbedder(), case(kind="how_to", expected_urls=[LABELS]), "t1"
    )

    assert (outcome.passed, outcome.outcome, outcome.cited_urls) == (
        True,
        Outcome.ANSWERED,
        [LABELS],
    )
    assert (outcome.top_articles, outcome.in_top_k) == ([LABELS, FORWARDERS], True)
    # One answer call, which the fake bills as Haiku: 1,000 tokens in and 200 out
    assert (outcome.claude_calls, outcome.cost_usd) == (1, 0.0002)


@pytest.mark.db
async def test_archiving_for_a_case_is_never_committed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await help_center(session)
    api = FakeClaude(draft("unused", []))

    outcome = await run_case(
        session_factory,
        make_claude(api),
        FakeEmbedder(),
        case(kind="archived", archive_urls=[LABELS]),
        "t2",
    )

    # With Text Labels archived, nothing close to the question is left: an honest no
    assert (outcome.passed, outcome.outcome, outcome.top_articles) == (
        True,
        Outcome.LOW_RETRIEVAL,
        [FORWARDERS],
    )
    async with session_factory() as session:
        status = await session.scalar(select(Page.status).where(Page.url == LABELS))
    assert status == PageStatus.ACTIVE
