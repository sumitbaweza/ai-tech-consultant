"""Task 3's tables as typed SQLAlchemy models. Every table lives in the `scope` schema.

A project holds meetings; each meeting gets a run of the agent team; each run saves every
agent's output as a step, and ends in a scope version a human approves. Step outputs are the
handoff objects from app/handoffs.py, stored as JSON, so the screen can show each handoff.

Status columns store a Python enum as plain text with a CHECK constraint, as in Task 1.
There are no relationship() attributes: queries join explicitly, so no hidden lazy loads.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine


class Base(DeclarativeBase):
    # The naming convention gives constraints and indexes predictable names, which later
    # migrations need in order to change them.
    metadata = MetaData(
        schema="scope",
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        },
    )
    type_annotation_map: ClassVar[dict[type[Any], TypeEngine[Any]]] = {
        datetime: DateTime(timezone=True),
        str: Text(),
        dict[str, Any]: JSONB(),
    }


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_class]


def text_enum(enum_class: type[StrEnum]) -> Enum:
    """Column type that stores a StrEnum's values as text, guarded by a CHECK constraint."""
    return Enum(
        enum_class,
        name=enum_class.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=_enum_values,
    )


# --- Enums --------------------------------------------------------------------------------


class RunKind(StrEnum):
    FIRST = "first"  # a first meeting: the full 8-step team, ending in scope v1
    FOLLOWUP = "followup"  # a later meeting: steps 1-3, then the change analyst


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_ANSWERS = "awaiting_answers"  # the consultant's questions wait for a human
    AWAITING_APPROVAL = "awaiting_approval"  # a draft scope waits for a human
    APPROVED = "approved"
    FAILED = "failed"


class StepStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class VersionStatus(StrEnum):
    DRAFT = "draft"  # nothing leaves the building in this state
    APPROVED = "approved"  # a human approved it; the proposal can go to the client
    SUPERSEDED = "superseded"  # a later approved version replaced it; kept for history


# --- Tables -------------------------------------------------------------------------------


class Project(Base):
    """One client engagement. Its meetings build up its scope versions."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    client: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Meeting(Base):
    """A transcript as it was uploaded. It is data: no line of it is ever an instruction."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str]
    transcript: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index(None, "project_id"),)


class Run(Base):
    """One pass of the agent team over one meeting, from transcript to a draft scope."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    kind: Mapped[RunKind] = mapped_column(text_enum(RunKind))
    status: Mapped[RunStatus] = mapped_column(text_enum(RunStatus))
    # Follow-ups: the approved version the meeting is compared with
    base_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("scope_versions.id", use_alter=True)
    )
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index(None, "project_id"),)
    # Read updated_at back in the same statement that sets it, so it's never a stale attribute
    # that async SQLAlchemy would have to load lazily (which it can't)
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Step(Base):
    """One agent's work in a run, or one human action, with its output as JSON.

    A step that runs again (the estimator after the reviewer sends it back, or a re-run after a
    failure) adds a new row, so the history stays; the latest row for an agent is current.
    """

    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    # splitter, extractor, ..., or human_answers, human_edit, human_approval, human_send_back
    agent: Mapped[str] = mapped_column(String(40))
    status: Mapped[StepStatus] = mapped_column(text_enum(StepStatus))
    output: Mapped[dict[str, Any] | None]
    error: Mapped[str | None]
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]

    __table_args__ = (Index(None, "run_id", "agent"),)


class ScopeVersion(Base):
    """A numbered scope for a project: v1 from the first meeting, v2 from a follow-up.

    Earlier versions are never changed once approved: a follow-up makes a new row.
    """

    __tablename__ = "scope_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    version: Mapped[int]
    status: Mapped[VersionStatus] = mapped_column(text_enum(VersionStatus))
    # An app.handoffs.Scope, as JSON
    scope: Mapped[dict[str, Any]]
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("project_id", "version"),)
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 (see Run)


class LLMCall(Base):
    """One row per Claude call: the ledger behind every cost and speed number."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The prompt file that ran, e.g. "extract_requirements"
    prompt: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[int | None]
    # The model that answered, which a refusal fallback can change from the one requested
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int]
    success: Mapped[bool]
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None]
    # Groups calls: "run-12/extractor" ties a cost to one run and one agent
    run_tag: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index(None, "run_tag"),)
