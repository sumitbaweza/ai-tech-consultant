"""Alembic runs the scope team's migrations with the app's own settings and models.

Only the `scope` schema is compared and migrated; the brain and inbox schemas belong to the
other tasks (and the `scope` role can't read them anyway). Alembic's own version table lives
in `scope` too.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.models import Base

SCHEMA = "scope"

config = context.config
if config.config_file_name is not None:
    # Keep loggers that already exist (pytest's, uvicorn's) working
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """When comparing models with the database, look at the `scope` schema only."""
    if type_ == "schema":
        return name == SCHEMA
    return True


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # search_path=public makes Postgres name every scope object in full, as scope.<name>, so
    # the models and the database compare by full name (the same trick as the Brain's).
    engine = create_async_engine(
        get_settings().database_url,
        connect_args={"server_settings": {"search_path": "public"}},
    )
    async with engine.connect() as connection:
        # Alembic's API is synchronous, so it runs inside the async connection's run_sync
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Migrations run against a live database only; --sql mode isn't set up.")

asyncio.run(run_async_migrations())
