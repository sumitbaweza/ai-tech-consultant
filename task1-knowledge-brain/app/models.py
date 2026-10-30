"""The Brain's tables as typed SQLAlchemy models. Every table lives in the `brain` schema.

Each status or kind column holds a Python enum stored as plain text, with a CHECK constraint
listing the allowed values. SQL stays readable (`WHERE status = 'active'`), and the database
rejects a value the code doesn't know.

There are no relationship() attributes: queries join tables explicitly, which avoids hidden
lazy loads (async SQLAlchemy raises an error on those).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

# voyage-4-lite returns 1,024 numbers per text by default
EMBEDDING_DIMENSIONS = 1024


class Base(DeclarativeBase):
    # Every table goes in the `brain` schema. The naming convention gives constraints and
    # indexes predictable names, which later migrations need in order to change them.
    metadata = MetaData(
        schema="brain",
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
        },
    )
    # Default column types: every datetime keeps its time zone, every str is unlimited text
    type_annotation_map: ClassVar[dict[type[Any], TypeEngine[Any]]] = {
        datetime: DateTime(timezone=True),
        str: Text(),
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


class Schedule(StrEnum):
    """How often a sitemap is checked again: test 2's four choices."""

    DAILY = "daily"
    WEEKLY = "weekly"
    EVERY_15_DAYS = "every_15_days"
    EVERY_30_DAYS = "every_30_days"

    @property
    def interval(self) -> timedelta:
        return timedelta(days=SCHEDULE_DAYS[self])


class Retention(StrEnum):
    """Test 2's archive rules: how many sitemap checks in a row may miss a URL before its
    page moves from the active knowledge base to the archived one."""

    IMMEDIATE = "immediate"
    AFTER_2 = "after_2"
    AFTER_15 = "after_15"
    AFTER_30 = "after_30"

    @property
    def missing_checks(self) -> int:
        return RETENTION_MISSING_CHECKS[self]


SCHEDULE_DAYS = {
    Schedule.DAILY: 1,
    Schedule.WEEKLY: 7,
    Schedule.EVERY_15_DAYS: 15,
    Schedule.EVERY_30_DAYS: 30,
}

# "Immediately" means the first check that misses the URL archives the page.
RETENTION_MISSING_CHECKS = {
    Retention.IMMEDIATE: 1,
    Retention.AFTER_2: 2,
    Retention.AFTER_15: 15,
    Retention.AFTER_30: 30,
}


class PageKind(StrEnum):
    ARTICLE = "article"
    # Home, category and folder pages: tracked so the counts match the sitemap, never embedded
    LISTING = "listing"
    # An expert's answer saved as knowledge, so search can find and cite it (Day 2)
    EXPERT = "expert"


class PageStatus(StrEnum):
    DISCOVERED = "discovered"  # listed in a sitemap; the user hasn't confirmed the ingestion yet
    PENDING = "pending"  # confirmed and queued, never fetched yet
    ACTIVE = "active"  # fetched and parsed: the only status search ever uses
    FAILED = "failed"  # still failing after retries; `error` says why
    REDIRECTED = "redirected"  # answered 301; `redirected_to_id` is the new page
    NOT_FOUND = "not_found"  # answered 404 or 410
    ARCHIVED = "archived"  # missing from the sitemap past the retention rule; kept, never deleted


class ChunkKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"


class RunKind(StrEnum):
    INITIAL = "initial"  # the first crawl, after the user confirms the preview
    REFRESH = "refresh"  # a scheduled re-check of a sitemap


class RunStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExpertReason(StrEnum):
    LOW_RETRIEVAL = "low_retrieval"  # search found nothing close enough to the question
    NOT_IN_SOURCES = "not_in_sources"  # the model said the sources don't answer it
    NO_VALID_CITATION = "no_valid_citation"  # the model answered but cited nothing it was given
    THUMBS_DOWN = "thumbs_down"  # a user marked the answer as wrong


class ExpertStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


# --- Tables -------------------------------------------------------------------------------


class Sitemap(Base):
    """A sitemap the user added, or one found inside a sitemap index."""

    __tablename__ = "sitemaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    # Set when this sitemap was found inside a sitemap index; null for one the user added
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sitemaps.id"))
    is_index: Mapped[bool] = mapped_column(server_default="false")
    schedule: Mapped[Schedule] = mapped_column(text_enum(Schedule))
    retention: Mapped[Retention] = mapped_column(text_enum(Retention))
    # <url> or <sitemap> entries in the file at the last read, duplicates inside it included
    entry_count: Mapped[int] = mapped_column(server_default="0")
    last_checked_at: Mapped[datetime | None]
    # Null until the user confirms an ingestion; the worker re-checks sitemaps that are due
    next_check_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class IngestionRun(Base):
    """One confirmed ingestion (the first crawl) or one scheduled re-check."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[RunKind] = mapped_column(text_enum(RunKind))
    status: Mapped[RunStatus] = mapped_column(text_enum(RunStatus))
    sitemap_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    # Pages this run queued; progress is how many of them are done
    total_pages: Mapped[int] = mapped_column(server_default="0")
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None]


class Page(Base):
    """One URL from a sitemap, with its latest content and where it is in the pipeline."""

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[PageKind] = mapped_column(text_enum(PageKind))
    status: Mapped[PageStatus] = mapped_column(text_enum(PageStatus), index=True)
    title: Mapped[str | None]
    category: Mapped[str | None]
    folder: Mapped[str | None]
    content_md: Mapped[str | None]
    # SHA-256 of the cleaned title and body. The page changed only if this changed.
    content_hash: Mapped[str | None] = mapped_column(String(64))
    # content_hash when the text chunks were last built; a different value means "re-embed"
    embedded_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None]
    # Sent back on the next fetch, so an unchanged page can answer 304 Not Modified
    etag: Mapped[str | None]
    last_modified: Mapped[str | None]
    sitemap_lastmod: Mapped[datetime | None]
    # Sitemap checks in a row that didn't list this URL, compared with the retention rule
    missing_count: Mapped[int] = mapped_column(server_default="0")
    redirected_to_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id"))
    # The work queue. fetch_requested_at set: waiting for a fetch or re-fetch. claimed_at: when
    # a worker took the page. A claim older than a few minutes means that worker died, so
    # another worker may take the page: that is how a stopped crawl resumes.
    fetch_requested_at: Mapped[datetime | None]
    claimed_at: Mapped[datetime | None]
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    # Failed fetches in a row; the worker gives up after a few
    attempts: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None]
    fetched_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PageSitemap(Base):
    """One row per sitemap that lists a URL. Two rows for one page means a duplicate URL."""

    __tablename__ = "page_sitemaps"

    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), primary_key=True)
    sitemap_id: Mapped[int] = mapped_column(ForeignKey("sitemaps.id"), primary_key=True, index=True)
    # Order in the sitemap file, so the preview can show its first and last URL
    position: Mapped[int]
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PageVersion(Base):
    """The change history: a copy of a page each time its content hash changed."""

    __tablename__ = "page_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None]
    content_md: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Image(Base):
    """A screenshot in an article, with Claude's description of what it shows."""

    __tablename__ = "images"
    __table_args__ = (UniqueConstraint("page_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"))
    # Order in the article
    position: Mapped[int]
    url: Mapped[str]
    alt: Mapped[str | None]
    # The paragraph around the image, given to the model as context
    context: Mapped[str | None]
    # SHA-256 of the image bytes: the same screenshot is never described (or paid for) twice
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    description: Mapped[str | None]
    # Model and prompt version that wrote the description, e.g. "claude-haiku-5-5 describe_image@v1"
    described_by: Mapped[str | None] = mapped_column(String(100))
    # Set while the description waits in a Message Batch, so polling survives a restart
    batch_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Chunk(Base):
    """A searchable piece of an article, or one screenshot's description, with its embedding."""

    __tablename__ = "chunks"
    __table_args__ = (Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    # Set for a screenshot's chunk; deleting the image row deletes its chunk too
    image_id: Mapped[int | None] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    kind: Mapped[ChunkKind] = mapped_column(text_enum(ChunkKind))
    position: Mapped[int]
    # Starts with "Category > Folder > Title", so every chunk carries its place in the help center
    content: Mapped[str]
    token_count: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(50))
    # Full-text search terms, computed and stored by Postgres whenever content changes
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ExpertTask(Base):
    """A question the Brain couldn't answer, waiting for a human expert (used from Day 2)."""

    __tablename__ = "expert_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str]
    # The chat turns before the question, so the expert sees the whole conversation
    history: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    # What search returned at the time: chunk ids and scores
    retrieved: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    reason: Mapped[ExpertReason] = mapped_column(text_enum(ExpertReason))
    status: Mapped[ExpertStatus] = mapped_column(text_enum(ExpertStatus))
    answer: Mapped[str | None]
    answered_by: Mapped[str | None]
    answered_at: Mapped[datetime | None]
    # The expert's answer saved as a page (kind "expert"), so search can cite it next time
    answer_page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LLMCall(Base):
    """One row per model call, Claude or Voyage: the ledger behind every cost and speed number."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The prompt file that ran, e.g. "answer"; for embeddings "embed_documents" or "embed_query"
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
    is_batch: Mapped[bool] = mapped_column(server_default="false")
    success: Mapped[bool]
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None]
    # Free-form label that groups calls, such as one eval run
    run_tag: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
