"""Settings for the scope team, read from environment variables.

In Docker, Compose passes the variables in. Locally, the repo's .env file is read too, so
`uv run` connects with the same passwords as the database container. A real environment
variable always wins over the .env file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# The .env file is at the repo root: app/ -> task3-scope-agents/ -> repo root.
# In the Docker image it doesn't exist, and pydantic-settings skips a missing file.
REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV_FILE, extra="ignore")

    # Postgres. Task 3 always connects as the `scope` role, which owns the `scope` schema and
    # can't read the Brain's tables. 127.0.0.1, not localhost: on Windows, localhost tries IPv6
    # first, where nothing listens, and each refused connection costs about 2 seconds.
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "consultant"
    scope_db_password: SecretStr = SecretStr("scope")

    # Comma-separated keys allowed to call this task's /v1, sent in the X-API-Key header
    scope_api_keys: SecretStr = SecretStr("dev-scope-key")
    # Where the screens find this task's API: in Compose, the API container by name
    scope_api_url: str = "http://127.0.0.1:8002"

    # The Knowledge Brain (Task 1), reached over HTTP only. The key is one of its BRAIN_API_KEYS.
    brain_api_url: str = "http://127.0.0.1:8000"
    brain_api_key: SecretStr = SecretStr("")

    # Claude. Empty until you add it to .env; runs fail with a clear error until then.
    anthropic_api_key: SecretStr = SecretStr("")

    # US dollars per hour for each role, used to price tasks. As JSON in the environment:
    # SCOPE_RATES='{"developer": 60}'
    scope_rates: dict[str, float] = Field(
        default_factory=lambda: {
            "developer": 50.0,
            "designer": 45.0,
            "consultant": 60.0,
            "qa": 35.0,
        }
    )
    # How many times the reviewer may send the estimate back before a human sees it
    max_review_rounds: int = Field(default=1, ge=0, le=3)
    # Transcript chunks extracted at the same time, and requirements checked against the Brain
    # at the same time: enough to be quick, few enough to stay under rate limits
    llm_concurrency: int = Field(default=4, ge=1, le=16)

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password; an f-string would not.
        return URL.create(
            "postgresql+asyncpg",
            username="scope",
            password=self.scope_db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def api_keys(self) -> frozenset[str]:
        raw = self.scope_api_keys.get_secret_value()
        return frozenset(key.strip() for key in raw.split(",") if key.strip())


@lru_cache
def get_settings() -> Settings:
    """One Settings object per process. Tests call get_settings.cache_clear() to change it."""
    return Settings()
