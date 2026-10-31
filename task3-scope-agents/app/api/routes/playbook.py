"""The estimation playbook: its versions, whether it helps, and rollback."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app import store
from app.api.deps import SessionDep
from app.handoffs import EstimationRule, Scope
from app.models import PlaybookVersion, ScopeVersion, VersionStatus

router = APIRouter(tags=["playbook"])


class PlaybookVersionView(BaseModel):
    version: int
    rules: list[EstimationRule]
    learned_from_id: int | None  # the approved scope version it was learned from
    is_active: bool
    created_at: datetime


class HoursGap(BaseModel):
    """How far the estimator's hours were from the approved ones, for the scopes estimated
    under one playbook version (None: before any playbook)."""

    playbook_version: int | None
    scopes: int
    ai_hours: float  # the estimator's expected hours, over the tasks it priced
    approved_hours: float  # the approved expected hours for the same tasks
    gap_percent: float  # sum of |approved - AI| per task, over the approved hours


class PlaybookView(BaseModel):
    active_version: int | None
    versions: list[PlaybookVersionView]
    gaps: list[HoursGap]


def hours_gaps(scopes: list[Scope]) -> list[HoursGap]:
    """The AI-vs-approved gap per playbook version: the number that says whether learning
    makes estimates better. Lower is better."""
    by_version: dict[int | None, list[Scope]] = {}
    for scope in scopes:
        by_version.setdefault(scope.playbook_version, []).append(scope)
    gaps: list[HoursGap] = []
    for version, group in sorted(by_version.items(), key=lambda item: item[0] or 0):
        priced = [t for s in group for t in s.tasks if t.ai_hours is not None]
        ai = sum(t.ai_hours.expected for t in priced if t.ai_hours is not None)
        approved = sum(t.expected for t in priced)
        off = sum(abs(t.expected - t.ai_hours.expected) for t in priced if t.ai_hours is not None)
        gaps.append(
            HoursGap(
                playbook_version=version,
                scopes=len(group),
                ai_hours=round(ai, 1),
                approved_hours=round(approved, 1),
                gap_percent=round(100 * off / approved, 1) if approved else 0.0,
            )
        )
    return gaps


async def _view(session: SessionDep) -> PlaybookView:
    rows = list(await session.scalars(select(PlaybookVersion).order_by(PlaybookVersion.version)))
    approved = await session.scalars(
        select(ScopeVersion.scope).where(ScopeVersion.status != VersionStatus.DRAFT)
    )
    return PlaybookView(
        active_version=next((row.version for row in rows if row.is_active), None),
        versions=[
            PlaybookVersionView(
                version=row.version,
                rules=[EstimationRule.model_validate(rule) for rule in row.rules["rules"]],
                learned_from_id=row.learned_from_id,
                is_active=row.is_active,
                created_at=row.created_at,
            )
            for row in rows
        ],
        gaps=hours_gaps([Scope.model_validate(scope) for scope in approved]),
    )


@router.get("/playbook")
async def get_playbook(session: SessionDep) -> PlaybookView:
    """Every playbook version with its rules, and the AI-vs-approved hours gap of the scopes
    estimated under each one."""
    return await _view(session)


@router.post("/playbook/{version}/activate")
async def activate(version: int, session: SessionDep) -> PlaybookView:
    """Make an older (or newer) playbook version the one the estimator follows: the undo for
    a lesson that made estimates worse."""
    await store.activate_playbook(session, version)
    return await _view(session)
