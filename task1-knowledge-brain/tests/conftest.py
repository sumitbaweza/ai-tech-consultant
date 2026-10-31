"""Shared test setup: the test database, known API keys, and skipping database tests.

pytest loads this file before any test module, so the environment below is in place
before app.config reads it. Tests never touch the main `consultant` database.
"""

import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ["DB_NAME"] = "consultant_test"
os.environ["BRAIN_API_KEYS"] = "test-key-1,test-key-2"
# Tests talk to fake sites, so there's no server to be polite to
os.environ["CRAWLER_REQUESTS_PER_SECOND"] = "1000"
# Never the real keys from .env: a test that forgets a fake fails instead of spending money
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["VOYAGE_API_KEY"] = ""

# Imported only now, after the environment above is set
from app.config import get_settings
from app.models import Base

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _postgres_is_running() -> bool:
    settings = get_settings()
    try:
        with socket.create_connection((settings.db_host, settings.db_port), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked `db` when Postgres isn't reachable, instead of failing them."""
    if _postgres_is_running():
        return
    skip = pytest.mark.skip(reason="Postgres isn't running: start it with `make up`")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Bring the test database's brain schema to the latest migration, once per run."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


@pytest.fixture
async def session_factory(
    migrated_database: None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on a fresh engine, with every Brain table emptied first.

    NullPool keeps no connection open between tests: each test runs in its own event loop,
    and an asyncpg connection can't move from one loop to another.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    tables = ", ".join(table.fullname for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
