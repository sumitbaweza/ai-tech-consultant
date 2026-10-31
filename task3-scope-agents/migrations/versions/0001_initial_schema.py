"""Initial schema: projects, meetings, runs, steps, scope versions and the LLM ledger.

Rendered from app/models.py with Alembic's autogenerate, then tidied. runs.base_version_id and
scope_versions.run_id point at each other, so that one foreign key is added after both tables
exist. tests/test_migrations.py checks that this file and the models still match.

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "scope"


def text_enum(name: str, *values: str) -> sa.Enum:
    """Same column type as app.models.text_enum: text with a CHECK constraint of the values."""
    return sa.Enum(
        *values, name=name, native_enum=False, create_constraint=True, length=32, schema=SCHEMA
    )


def timestamp(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
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
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_tag", sa.String(length=100), nullable=True),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_llm_calls_run_tag"), "llm_calls", ["run_tag"], schema=SCHEMA)

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("client", sa.Text(), nullable=False),
        timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        schema=SCHEMA,
    )

    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["scope.projects.id"],
            name=op.f("fk_meetings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meetings")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_meetings_project_id"), "meetings", ["project_id"], schema=SCHEMA)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("kind", text_enum("runkind", "first", "followup"), nullable=False),
        sa.Column(
            "status",
            text_enum(
                "runstatus",
                "queued",
                "running",
                "awaiting_answers",
                "awaiting_approval",
                "approved",
                "failed",
            ),
            nullable=False,
        ),
        sa.Column("base_version_id", sa.Integer(), nullable=True),  # foreign key added below
        sa.Column("error", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["meeting_id"],
            ["scope.meetings.id"],
            name=op.f("fk_runs_meeting_id_meetings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["scope.projects.id"],
            name=op.f("fk_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_runs_project_id"), "runs", ["project_id"], schema=SCHEMA)

    op.create_table(
        "scope_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status", text_enum("versionstatus", "draft", "approved", "superseded"), nullable=False
        ),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["scope.projects.id"],
            name=op.f("fk_scope_versions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["scope.runs.id"],
            name=op.f("fk_scope_versions_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scope_versions")),
        sa.UniqueConstraint(
            "project_id", "version", name=op.f("uq_scope_versions_project_id_version")
        ),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_runs_base_version_id_scope_versions"),
        "runs",
        "scope_versions",
        ["base_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        "steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("status", text_enum("stepstatus", "running", "done", "failed"), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        timestamp("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["scope.runs.id"], name=op.f("fk_steps_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_steps")),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_steps_run_id_agent"), "steps", ["run_id", "agent"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("steps", schema=SCHEMA)
    op.drop_constraint(
        op.f("fk_runs_base_version_id_scope_versions"), "runs", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_table("scope_versions", schema=SCHEMA)
    op.drop_table("runs", schema=SCHEMA)
    op.drop_table("meetings", schema=SCHEMA)
    op.drop_table("projects", schema=SCHEMA)
    op.drop_table("llm_calls", schema=SCHEMA)
