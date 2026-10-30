"""expert_tasks: one task per repeated question, how often it was asked, and a thumbs down's
answer.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Open or dismissed tasks: the ones a repeat of the same question counts on
OPEN_OR_DISMISSED = sa.text("status IN ('open', 'dismissed')")


def upgrade() -> None:
    # Nothing wrote expert tasks before this revision, so the table is empty and a NOT NULL
    # column needs no default
    op.add_column(
        "expert_tasks", sa.Column("question_key", sa.Text(), nullable=False), schema="brain"
    )
    op.add_column(
        "expert_tasks",
        sa.Column("asked_count", sa.Integer(), server_default="1", nullable=False),
        schema="brain",
    )
    op.add_column(
        "expert_tasks",
        sa.Column(
            "last_asked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="brain",
    )
    op.add_column(
        "expert_tasks", sa.Column("given_answer", sa.Text(), nullable=True), schema="brain"
    )
    op.create_index(
        op.f("ix_expert_tasks_question_key"),
        "expert_tasks",
        ["question_key"],
        unique=True,
        schema="brain",
        postgresql_where=OPEN_OR_DISMISSED,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_expert_tasks_question_key"), table_name="expert_tasks", schema="brain")
    for column in ("given_answer", "last_asked_at", "asked_count", "question_key"):
        op.drop_column("expert_tasks", column, schema="brain")
