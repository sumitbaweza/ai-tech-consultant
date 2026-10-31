"""Answer a question from the help center, with citations, or say honestly that it can't.

ask() runs these steps:
1. A follow-up such as "what about members?" is rewritten by Haiku into a question that stands
   on its own, using the chat so far (prompts/rewrite_question).
2. Hybrid search finds the MAX_SOURCES best chunks.
3. If none of them is close in meaning (cosine similarity under MIN_SIMILARITY), the help center
   doesn't cover the question: the answer is "I don't know", and Sonnet is never called.
4. The router (app/rag/router.py) picks the model. An easy question goes to Haiku
   (prompts/answer_fast); any other to Sonnet (prompts/answer). Either answers from those chunks
   only, citing them by id. A Haiku answer that is unanswered, uncited or unsure is thrown away,
   and Sonnet answers instead.
5. Citation ids that don't match a source are dropped. An answer with no valid citation left
   can't be checked, so it becomes "I don't know" too.

Every result has an outcome saying why it was or wasn't answered, the route (which model
answered and why), and the sources search found. ask() only reads: for an unanswered question,
POST /v1/ask opens an expert task (app/experts.py), so evals and other callers can ask without
filling the queue.
"""

import logging
from enum import StrEnum
from typing import Literal

import anthropic
from anthropic.types.beta import BetaContentBlockParam
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import Claude, LLMError
from app.models import PageKind
from app.rag.embed import Embedder, EmbeddingError
from app.rag.router import Route, RouteMode, Tier, choose_route, escalate
from app.rag.search import SearchHit, search

logger = logging.getLogger(__name__)

MAX_SOURCES = 8
# Below this cosine similarity, nothing in the help center is about the question. A starting
# point, to be tuned on the eval set.
MIN_SIMILARITY = 0.30
SNIPPET_CHARS = 300
# A Haiku answer less sure than this goes to Sonnet instead. A starting point, like the others.
ESCALATE_BELOW = 0.7

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


# Where a source's text comes from: a help-center article, or a human expert's answer saved
# from the expert queue
Origin = Literal["article", "expert"]


class Citation(BaseModel):
    source_id: str
    origin: Origin  # "expert": show it as an expert answer, not an article link
    chunk_id: int
    page_id: int
    title: str | None
    url: str
    image_url: str | None  # set when the cited source is a screenshot
    snippet: str


class Source(BaseModel):
    """A chunk that search found for the question, cited or not."""

    origin: Origin
    chunk_id: int
    page_id: int
    title: str | None
    url: str
    similarity: float | None  # cosine similarity to the question, if vector search found it
    score: float  # the fused rank score: higher is better


class AskResult(BaseModel):
    question: str
    standalone_question: str  # the question that was searched: a follow-up, rewritten
    answered: bool
    answer: str
    citations: list[Citation]
    confidence: float
    outcome: Outcome
    sources: list[Source]  # everything search found, best first
    # Which model answered and why; None when no model was called (nothing close was found)
    route: Route | None = None
    # The expert task the question went to, when the help center didn't answer it
    expert_task_id: int | None = None


async def ask(
    session: AsyncSession,
    claude: Claude,
    embedder: Embedder | None,
    question: str,
    history: list[ChatTurn],
    *,
    run_tag: str | None = None,
    mode: RouteMode = "auto",
) -> AskResult:
    """`run_tag` labels this question's Claude calls in llm_calls, so an eval can add up what
    each question cost. `mode` "sonnet" or "haiku" skips the router, for comparing models."""
    standalone = await _standalone(claude, question, history, run_tag) if history else question

    query_vector: list[float] | None = None
    if embedder is not None:
        try:
            [query_vector] = await embedder.embed([standalone], "query")
        except EmbeddingError:
            logger.warning("Embedding the question failed; searching by exact words only")
    hits = await search(session, standalone, query_vector, limit=MAX_SOURCES)
    found = [make_source(hit) for hit in hits]

    if not hits or (query_vector is not None and best_similarity(hits) < MIN_SIMILARITY):
        return _unanswered(question, standalone, Outcome.LOW_RETRIEVAL, DONT_KNOW, found)

    sources = {f"S{number}": hit for number, hit in enumerate(hits, start=1)}
    content = answer_content(standalone, sources)
    route = choose_route(standalone, hits, mode)
    draft = await _draft(claude, route.prompt, content, run_tag)
    if route.tier == Tier.HAIKU and route.mode == "auto":
        weakness = _weakness(draft, sources)
        if weakness is not None:
            route = escalate(route, weakness)
            draft = await _draft(claude, route.prompt, content, run_tag)
    logger.info("Answered by %s: %s", route.tier, route.reason)

    if draft is None:
        return _unanswered(question, standalone, Outcome.MODEL_FAILED, FAILED, found, route)
    cited = valid_citations(draft.citations, sources)
    if not draft.answered:
        return _unanswered(question, standalone, Outcome.NOT_IN_SOURCES, draft.answer, found, route)
    if not cited:
        return _unanswered(question, standalone, Outcome.NO_VALID_CITATION, DONT_KNOW, found, route)
    return AskResult(
        question=question,
        standalone_question=standalone,
        answered=True,
        answer=draft.answer,
        citations=[make_citation(source_id, sources[source_id]) for source_id in cited],
        confidence=draft.confidence,
        outcome=Outcome.ANSWERED,
        sources=found,
        route=route,
    )


async def _draft(
    claude: Claude, prompt: str, content: list[BetaContentBlockParam], run_tag: str | None
) -> AnswerDraft | None:
    """One answer from the given prompt, or None when the model gave no usable answer."""
    try:
        return await claude.call(prompt, 1, content, AnswerDraft, run_tag=run_tag)
    except (LLMError, anthropic.APIError) as error:
        logger.warning("Answering with %s failed: %s", prompt, error)
        return None


def _weakness(draft: AnswerDraft | None, sources: dict[str, SearchHit]) -> str | None:
    """Why Haiku's answer isn't good enough to send, or None when it is."""
    if draft is None:
        return "haiku gave no usable answer"
    if not draft.answered:
        return "haiku found no answer in the sources"
    if not valid_citations(draft.citations, sources):
        return "haiku cited none of the sources"
    if draft.confidence < ESCALATE_BELOW:
        return f"haiku's confidence was {draft.confidence:.2f}"
    return None


def valid_citations(citations: list[str], sources: dict[str, SearchHit]) -> list[str]:
    """The cited ids that are real sources. dict.fromkeys keeps the first mention of each id,
    in order; ids we didn't give are dropped."""
    return [source_id for source_id in dict.fromkeys(citations) if source_id in sources]


def answer_content(question: str, sources: dict[str, SearchHit]) -> list[BetaContentBlockParam]:
    """The sources, then the question. The tags mark them as data."""
    text = f"{sources_block(sources)}\n\n<question>{question}</question>"
    return [{"type": "text", "text": text}]


def sources_block(sources: dict[str, SearchHit]) -> str:
    """Each source in a tag with its id, kind and title, inside one <sources> tag."""
    blocks: list[str] = []
    for source_id, hit in sources.items():
        kind = "screenshot" if hit.image_url else "text"
        blocks.append(
            f'<source id="{source_id}" kind="{kind}" title="{hit.title or ""}">\n'
            f"{hit.content}\n</source>"
        )
    return "<sources>\n" + "\n".join(blocks) + "\n</sources>"


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


def best_similarity(hits: list[SearchHit]) -> float:
    return max((hit.similarity for hit in hits if hit.similarity is not None), default=0.0)


def make_citation(source_id: str, hit: SearchHit) -> Citation:
    # Each chunk starts with its breadcrumb line; the snippet shows the text after it
    text = hit.content.split("\n\n", 1)[-1]
    return Citation(
        source_id=source_id,
        origin=_origin(hit),
        chunk_id=hit.chunk_id,
        page_id=hit.page_id,
        title=hit.title,
        url=hit.url,
        image_url=hit.image_url,
        snippet=text[:SNIPPET_CHARS],
    )


def make_source(hit: SearchHit) -> Source:
    return Source(
        origin=_origin(hit),
        chunk_id=hit.chunk_id,
        page_id=hit.page_id,
        title=hit.title,
        url=hit.url,
        similarity=hit.similarity,
        score=hit.score,
    )


def _origin(hit: SearchHit) -> Origin:
    return "expert" if hit.page_kind == PageKind.EXPERT else "article"


def _unanswered(
    question: str,
    standalone: str,
    outcome: Outcome,
    answer: str,
    found: list[Source],
    route: Route | None = None,
) -> AskResult:
    return AskResult(
        question=question,
        standalone_question=standalone,
        answered=False,
        answer=answer,
        citations=[],
        confidence=0.0,
        outcome=outcome,
        sources=found,
        route=route,
    )
