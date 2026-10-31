"""The estimation playbook: versioned rules learned from human hour edits.

Rendered from app/models.py with Alembic's autogenerate, then tidied.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbook_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("learned_from_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["learned_from_id"],
            ["scope.scope_versions.id"],
            name=op.f("fk_playbook_versions_learned_from_id_scope_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbook_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_playbook_versions_version")),
        schema="scope",
    )


def downgrade() -> None:
    op.drop_table("playbook_versions", schema="scope")
