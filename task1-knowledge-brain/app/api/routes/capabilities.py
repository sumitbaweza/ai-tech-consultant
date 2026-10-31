"""Can the product already do this? For a client requirement: yes, partly or no, with the
setting and the articles that show it."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ClaudeDep, SessionDep, VoyageDep
from app.rag.capability import CapabilityResult, check_capability

router = APIRouter(tags=["capabilities"])


class CapabilityRequest(BaseModel):
    requirement: str = Field(
        min_length=1,
        max_length=2000,
        description="What the client wants, in plain words, e.g. 'new members need approval "
        "before their listing goes live'",
    )


@router.post("/capabilities/check")
async def check(
    body: CapabilityRequest, session: SessionDep, claude: ClaudeDep, voyage: VoyageDep
) -> CapabilityResult:
    """`verdict` is yes (a built-in setting does it), partly (a setting covers some of it, and
    `explanation` says what's missing), no (the help center says it can't), or unknown (the
    help center doesn't say). `setting` names where the feature lives, and every verdict but
    unknown cites at least one article or screenshot. `outcome` says how the verdict was
    reached: checked, low_retrieval (nothing close in the help center), no_valid_citation or
    model_failed. Unlike /v1/ask, an unknown requirement doesn't go to the expert queue: a
    feature the product lacks isn't a gap in the help center."""
    return await check_capability(session, claude, voyage, body.requirement)
