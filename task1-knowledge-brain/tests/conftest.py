"""Shared test setup: the test database, known API keys, and skipping database tests.

pytest loads this file before any test module, so the environment below is in place
before app.config reads it. Tests never touch the main `consultant` database.
"""

import os
import socket

import pytest

os.environ["DB_NAME"] = "consultant_test"
os.environ["BRAIN_API_KEYS"] = "test-key-1,test-key-2"

# Imported only now, after the environment above is set
from app.config import get_settings


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
