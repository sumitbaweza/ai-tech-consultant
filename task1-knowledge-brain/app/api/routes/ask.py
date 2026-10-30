"""Ask the knowledge base a question and get an answer with citations, or an honest "I don't
know"."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ClaudeDep, SessionDep, VoyageDep
from app.experts import EXPERT_REASONS, open_task
from app.rag.answer import AskResult, ChatTurn, ask

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(
        default_factory=list[ChatTurn],
        max_length=20,
        description="Earlier turns of the chat, oldest first, so a follow-up can be understood",
    )


@router.post("/ask")
async def ask_question(
    body: AskRequest, session: SessionDep, claude: ClaudeDep, voyage: VoyageDep
) -> AskResult:
    """Answer from the help center only. `answered` is false when the help center doesn't cover
    the question, and `outcome` says why: low_retrieval (nothing close), not_in_sources (the
    closest articles don't answer it), no_valid_citation, or model_failed. Every citation
    points at an article (and a screenshot, if that's where the answer is), and `sources` lists
    everything search found.

    A question the help center doesn't cover goes to a human expert: `expert_task_id` is its
    task in GET /v1/expert-tasks. A model failure doesn't, since asking again later can work."""
    result = await ask(session, claude, voyage, body.question, body.history)
    reason = EXPERT_REASONS.get(result.outcome)
    if reason is not None:
        # The task keeps the question as it stands on its own. For a follow-up, the expert also
        # sees the chat, ending with the question in the user's own words.
        chat = [*body.history, ChatTurn(role="user", content=body.question)] if body.history else []
        task = await open_task(
            session,
            question=result.standalone_question,
            history=chat,
            reason=reason,
            sources=result.sources,
        )
        await session.commit()
        result.expert_task_id = task.id
    return result
