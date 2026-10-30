"""Settings for the Brain, read from environment variables.

In Docker, Compose passes the variables in. Locally, the repo's .env file is read too, so
`uv run` connects with the same passwords as the database container. A real environment
variable always wins over the .env file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# The .env file is at the repo root: app/ -> task1-knowledge-brain/ -> repo root.
# In the Docker image it doesn't exist, and pydantic-settings skips a missing file.
REPO_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ENV_FILE, extra="ignore")

    # Postgres. The Brain always connects as the `brain` role, which owns the `brain` schema.
    # 127.0.0.1 rather than localhost: on Windows, localhost tries IPv6 first, where nothing
    # listens, and each refused connection costs about 2 seconds.
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "consultant"
    brain_db_password: SecretStr = SecretStr("brain")

    # Comma-separated keys allowed to call /v1, sent in the X-API-Key header
    brain_api_keys: SecretStr = SecretStr("")

    # Claude. Empty until you add it to .env; calls fail with an auth error until then.
    anthropic_api_key: SecretStr = SecretStr("")
    # Voyage AI embeddings. Without it no chunks are embedded, and search uses exact words only.
    voyage_api_key: SecretStr = SecretStr("")

    # Crawler politeness: who we say we are, and the most requests per second to one host
    crawler_user_agent: str = "AITechConsultantBot/0.1 (polite help-center crawler)"
    crawler_requests_per_second: float = 1.0
    # Faster limits for hosts built to take more, such as the CDN that serves the screenshots.
    # As JSON in the environment: CRAWLER_HOST_RATES='{"s3.amazonaws.com": 2}'
    crawler_host_rates: dict[str, float] = Field(default_factory=lambda: {"s3.amazonaws.com": 2.0})

    @property
    def database_url(self) -> URL:
        # URL.create escapes special characters in the password; an f-string would not.
        return URL.create(
            "postgresql+asyncpg",
            username="brain",
            password=self.brain_db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def api_keys(self) -> frozenset[str]:
        raw = self.brain_api_keys.get_secret_value()
        return frozenset(key.strip() for key in raw.split(",") if key.strip())


@lru_cache
def get_settings() -> Settings:
    """One Settings object per process. Tests call get_settings.cache_clear() to change it."""
    return Settings()
