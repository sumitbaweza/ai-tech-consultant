"""Initial schema: sitemaps, pages, chunks, images, expert tasks and the LLM ledger.

Rendered from app/models.py with Alembic's code renderer, then tidied.
tests/test_migrations.py checks that this file and the models still match.

Revision ID: 0001
Revises:
Create Date: 2026-10-30
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def text_enum(name: str, *values: str) -> sa.Enum:
    """Same column type as app.models.text_enum: text with a CHECK constraint of the values."""
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", text_enum("runkind", "initial", "refresh"), nullable=False),
        sa.Column(
            "status",
            text_enum("runstatus", "running", "done", "failed", "cancelled"),
            nullable=False,
        ),
        sa.Column("sitemap_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("total_pages", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
        schema="brain",
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("is_batch", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_tag", sa.String(length=100), nullable=True),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
        schema="brain",
    )
    op.create_table(
        "sitemaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("is_index", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "schedule",
            text_enum("schedule", "daily", "weekly", "every_15_days", "every_30_days"),
            nullable=False,
        ),
        sa.Column(
            "retention",
            text_enum("retention", "immediate", "after_2", "after_15", "after_30"),
            nullable=False,
        ),
        sa.Column("entry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["brain.sitemaps.id"], name=op.f("fk_sitemaps_parent_id_sitemaps")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sitemaps")),
        sa.UniqueConstraint("url", name=op.f("uq_sitemaps_url")),
        schema="brain",
    )
    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", text_enum("pagekind", "article", "listing", "expert"), nullable=False),
        sa.Column(
            "status",
            text_enum(
                "pagestatus",
                "discovered",
                "pending",
                "active",
                "failed",
                "redirected",
                "not_found",
                "archived",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("folder", sa.Text(), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("embedded_hash", sa.String(length=64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("sitemap_lastmod", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("redirected_to_id", sa.Integer(), nullable=True),
        sa.Column("fetch_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["redirected_to_id"], ["brain.pages.id"], name=op.f("fk_pages_redirected_to_id_pages")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["brain.ingestion_runs.id"], name=op.f("fk_pages_run_id_ingestion_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pages")),
        sa.UniqueConstraint("url", name=op.f("uq_pages_url")),
        schema="brain",
    )
    op.create_index(op.f("ix_pages_status"), "pages", ["status"], unique=False, schema="brain")
    op.create_table(
        "expert_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("history", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("retrieved", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "reason",
            text_enum(
                "expertreason",
                "low_retrieval",
                "not_in_sources",
                "no_valid_citation",
                "thumbs_down",
            ),
            nullable=False,
        ),
        sa.Column(
            "status", text_enum("expertstatus", "open", "answered", "dismissed"), nullable=False
        ),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answer_page_id", sa.Integer(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["answer_page_id"],
            ["brain.pages.id"],
            name=op.f("fk_expert_tasks_answer_page_id_pages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expert_tasks")),
        schema="brain",
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("alt", sa.Text(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("described_by", sa.String(length=100), nullable=True),
        sa.Column("batch_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["page_id"], ["brain.pages.id"], name=op.f("fk_images_page_id_pages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_images")),
        sa.UniqueConstraint("page_id", "position", name=op.f("uq_images_page_id_position")),
        schema="brain",
    )
    op.create_index(op.f("ix_images_sha256"), "images", ["sha256"], unique=False, schema="brain")
    op.create_table(
        "page_sitemaps",
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("sitemap_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["brain.pages.id"], name=op.f("fk_page_sitemaps_page_id_pages")
        ),
        sa.ForeignKeyConstraint(
            ["sitemap_id"], ["brain.sitemaps.id"], name=op.f("fk_page_sitemaps_sitemap_id_sitemaps")
        ),
        sa.PrimaryKeyConstraint("page_id", "sitemap_id", name=op.f("pk_page_sitemaps")),
        schema="brain",
    )
    op.create_index(
        op.f("ix_page_sitemaps_sitemap_id"),
        "page_sitemaps",
        ["sitemap_id"],
        unique=False,
        schema="brain",
    )
    op.create_table(
        "page_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(
            ["page_id"], ["brain.pages.id"], name=op.f("fk_page_versions_page_id_pages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_versions")),
        schema="brain",
    )
    op.create_index(
        op.f("ix_page_versions_page_id"), "page_versions", ["page_id"], unique=False, schema="brain"
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=True),
        sa.Column("kind", text_enum("chunkkind", "text", "image"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("embedding_model", sa.String(length=50), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        created_at(),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["brain.images.id"],
            name=op.f("fk_chunks_image_id_images"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["brain.pages.id"], name=op.f("fk_chunks_page_id_pages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        schema="brain",
    )
    op.create_index(op.f("ix_chunks_page_id"), "chunks", ["page_id"], unique=False, schema="brain")
    op.create_index(
        "ix_chunks_tsv", "chunks", ["tsv"], unique=False, schema="brain", postgresql_using="gin"
    )


def downgrade() -> None:
    # Tables go in reverse order, so nothing is dropped while another table still points at it
    op.drop_index("ix_chunks_tsv", table_name="chunks", schema="brain", postgresql_using="gin")
    op.drop_index(op.f("ix_chunks_page_id"), table_name="chunks", schema="brain")
    op.drop_table("chunks", schema="brain")
    op.drop_index(op.f("ix_page_versions_page_id"), table_name="page_versions", schema="brain")
    op.drop_table("page_versions", schema="brain")
    op.drop_index(op.f("ix_page_sitemaps_sitemap_id"), table_name="page_sitemaps", schema="brain")
    op.drop_table("page_sitemaps", schema="brain")
    op.drop_index(op.f("ix_images_sha256"), table_name="images", schema="brain")
    op.drop_table("images", schema="brain")
    op.drop_table("expert_tasks", schema="brain")
    op.drop_index(op.f("ix_pages_status"), table_name="pages", schema="brain")
    op.drop_table("pages", schema="brain")
    op.drop_table("sitemaps", schema="brain")
    op.drop_table("llm_calls", schema="brain")
    op.drop_table("ingestion_runs", schema="brain")
