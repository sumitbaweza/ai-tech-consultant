"""The migration files and app/models.py must describe the same tables (needs Postgres)."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.mark.db
def test_migrations_run_both_ways_and_match_the_models() -> None:
    config = Config(str(ALEMBIC_INI))

    command.downgrade(config, "base")  # start empty, whatever an earlier run left behind
    command.upgrade(config, "head")
    command.check(config)  # raises if the models and the migrations differ
    command.downgrade(config, "base")  # every migration can be undone
    command.upgrade(config, "head")  # leave the tables for other database tests
