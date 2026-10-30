"""ingestion_runs: what each run found (new, changed, missing, archived and restored pages).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("new_pages", "changed_pages", "missing_pages", "archived_pages", "restored_pages")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column(
            "ingestion_runs",
            sa.Column(column, sa.Integer(), server_default="0", nullable=False),
            schema="brain",
        )
    # Every page a first crawl queued was new
    op.execute("UPDATE brain.ingestion_runs SET new_pages = total_pages WHERE kind = 'initial'")


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("ingestion_runs", column, schema="brain")
