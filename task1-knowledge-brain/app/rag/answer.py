"""Answer a question from the help center, with citations, or say honestly that it can't.

ask() runs these steps:
1. A follow-up such as "what about members?" is rewritten by Haiku into a question that stands
   on its own, using the chat so far (prompts/rewrite_question).
2. Hybrid search finds the MAX_SOURCES best chunks.
3. If none of them is close in meaning (cosine similarity under MIN_SIMILARITY), the help center
   doesn't cover the question: the answer is "I don't know", and Sonnet is never called.
4. Sonnet answers from those chunks only (prompts/answer), citing them by id.
5. Citation ids that don't match a source are dropped. An answer with no valid citation left
   can't be checked, so it becomes "I don't know" too.

Every result has an outcome saying why it was or wasn't answered. Unanswered outcomes are the
reasons an expert task is created for (from Day 2).
"""

import logging
from enum import StrEnum
from typing import Literal

import anthropic
from anthropic.types.beta import BetaContentBlockParam
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import Claude, LLMError
from app.rag.embed import Embedder, EmbeddingError
from app.rag.search import SearchHit, search

logger = logging.getLogger(__name__)

MAX_SOURCES = 8
# Below this cosine similarity, nothing in the help center is about the question. A starting
# point, to be tuned on the eval set.
MIN_SIMILARITY = 0.30
SNIPPET_CHARS = 300

DONT_KNOW = (
    "I couldn't find this in the help center, so I won't guess. A support expert can answer it."
)
FAILED = "I couldn't produce an answer just now. Please try again."


class Outcome(StrEnum):
    ANSWERED = "answered"
    LOW_RETRIEVAL = "low_retrieval"  # nothing in the help center came close to the question
    NOT_IN_SOURCES = "not_in_sources"  # Claude read the closest sources; they don't answer it
    NO_VALID_CITATION = "no_valid_citation"  # Claude answered, but cited none of its sources
    MODEL_FAILED = "model_failed"  # Claude refused, broke the format or wasn't reachable


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class StandaloneQuestion(BaseModel):
    """What Haiku returns for prompts/rewrite_question."""

    question: str


class AnswerDraft(BaseModel):
    """What Sonnet returns for prompts/answer."""

    answer: str
    citations: list[str]  # source ids, such as "S2"
    answered: bool
    confidence: float = Field(ge=0, le=1)


class Citation(BaseModel):
    source_id: str
    chunk_id: int
    page_id: int
    title: str | None
    url: str
    image_url: str | None  # set when the cited source is a screenshot
    snippet: str


class AskResult(BaseModel):
    question: str
    standalone_question: str  # the question that was searched: a follow-up, rewritten
    answered: bool
    answer: str
    citations: list[Citation]
    confidence: float
    outcome: Outcome


async def ask(
    session: AsyncSession,
    claude: Claude,
    embedder: Embedder | None,
    question: str,
    history: list[ChatTurn],
    *,
    run_tag: str | None = None,
) -> AskResult:
    """`run_tag` labels this question's Claude calls in llm_calls, so an eval can add up what
    each question cost."""
    standalone = await _standalone(claude, question, history, run_tag) if history else question

    query_vector: list[float] | None = None
    if embedder is not None:
        try:
            [query_vector] = await embedder.embed([standalone], "query")
        except EmbeddingError:
            logger.warning("Embedding the question failed; searching by exact words only")
    hits = await search(session, standalone, query_vector, limit=MAX_SOURCES)

    if not hits or (query_vector is not None and _best_similarity(hits) < MIN_SIMILARITY):
        return _unanswered(question, standalone, Outcome.LOW_RETRIEVAL, DONT_KNOW)

    sources = {f"S{number}": hit for number, hit in enumerate(hits, start=1)}
    try:
        draft = await claude.call(
            "answer", 1, answer_content(standalone, sources), AnswerDraft, run_tag=run_tag
        )
    except (LLMError, anthropic.APIError) as error:
        logger.warning("Answering failed: %s", error)
        return _unanswered(question, standalone, Outcome.MODEL_FAILED, FAILED)

    # dict.fromkeys keeps the first mention of each id, in order; ids we didn't give are dropped
    cited = [source_id for source_id in dict.fromkeys(draft.citations) if source_id in sources]
    if not draft.answered:
        return _unanswered(question, standalone, Outcome.NOT_IN_SOURCES, draft.answer)
    if not cited:
        return _unanswered(question, standalone, Outcome.NO_VALID_CITATION, DONT_KNOW)
    return AskResult(
        question=question,
        standalone_question=standalone,
        answered=True,
        answer=draft.answer,
        citations=[_citation(source_id, sources[source_id]) for source_id in cited],
        confidence=draft.confidence,
        outcome=Outcome.ANSWERED,
    )


def answer_content(question: str, sources: dict[str, SearchHit]) -> list[BetaContentBlockParam]:
    """The sources, each in a tag with its id, then the question. The tags mark them as data."""
    blocks: list[str] = []
    for source_id, hit in sources.items():
        kind = "screenshot" if hit.image_url else "text"
        blocks.append(
            f'<source id="{source_id}" kind="{kind}" title="{hit.title or ""}">\n'
            f"{hit.content}\n</source>"
        )
    text = "<sources>\n" + "\n".join(blocks) + f"\n</sources>\n\n<question>{question}</question>"
    return [{"type": "text", "text": text}]


def rewrite_content(history: list[ChatTurn], question: str) -> str:
    turns = "\n".join(f'<message role="{turn.role}">{turn.content}</message>' for turn in history)
    return f"<chat>\n{turns}\n</chat>\n\n<last_message>{question}</last_message>"


async def _standalone(
    claude: Claude, question: str, history: list[ChatTurn], run_tag: str | None
) -> str:
    """The follow-up as a question that stands on its own; the question as asked if that fails."""
    try:
        rewritten = await claude.call(
            "rewrite_question",
            1,
            rewrite_content(history, question),
            StandaloneQuestion,
            run_tag=run_tag,
        )
    except (LLMError, anthropic.APIError) as error:
        logger.warning("Rewriting the follow-up failed, so it's searched as asked: %s", error)
        return question
    return rewritten.question.strip() or question


def _best_similarity(hits: list[SearchHit]) -> float:
    return max((hit.similarity for hit in hits if hit.similarity is not None), default=0.0)


def _citation(source_id: str, hit: SearchHit) -> Citation:
    # Each chunk starts with its breadcrumb line; the snippet shows the text after it
    text = hit.content.split("\n\n", 1)[-1]
    return Citation(
        source_id=source_id,
        chunk_id=hit.chunk_id,
        page_id=hit.page_id,
        title=hit.title,
        url=hit.url,
        image_url=hit.image_url,
        snippet=text[:SNIPPET_CHARS],
    )


def _unanswered(question: str, standalone: str, outcome: Outcome, answer: str) -> AskResult:
    return AskResult(
        question=question,
        standalone_question=standalone,
        answered=False,
        answer=answer,
        citations=[],
        confidence=0.0,
        outcome=outcome,
    )
