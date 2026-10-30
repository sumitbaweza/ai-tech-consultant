"""page_sitemaps.removed_at: a sitemap that drops a URL keeps the row, marked removed.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page_sitemaps",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("page_sitemaps", "removed_at", schema="brain")
