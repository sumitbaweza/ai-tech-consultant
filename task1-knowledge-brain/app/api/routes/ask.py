"""Ask the knowledge base a question and get an answer with citations, or an honest "I don't
know"."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ClaudeDep, SessionDep, VoyageDep
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
    points at an article (and a screenshot, if that's where the answer is)."""
    return await ask(session, claude, voyage, body.question, body.history)
