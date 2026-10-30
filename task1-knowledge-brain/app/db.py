"""The async database engine and sessions, shared by the API and the worker."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# Creating the engine doesn't connect; the first query does. pool_pre_ping tests a pooled
# connection before handing it out, so a database restart doesn't break the next request.
engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    # A query with no answer after 60 seconds raises an error instead of waiting forever.
    # A connection can hang that way after the laptop wakes from sleep.
    connect_args={"command_timeout": 60},
)

# expire_on_commit=False keeps loaded rows readable after a commit. Otherwise reading an
# attribute would trigger a hidden reload, which async SQLAlchemy doesn't allow.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed when the request ends."""
    async with SessionFactory() as session:
        yield session
