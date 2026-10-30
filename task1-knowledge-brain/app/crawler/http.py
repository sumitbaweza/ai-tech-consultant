"""The HTTP client every crawler request uses: who we are, timeouts, and a size cap.

httpx2 is the maintained fork of httpx. The Anthropic SDK already depends on it, so the Brain
has one HTTP library.
"""

import httpx2

from app.config import Settings

# sitemaps.org caps a sitemap at 50 MB uncompressed; help pages are far smaller
MAX_BODY_BYTES = 50 * 1024 * 1024


class BodyTooLarge(Exception):
    """The response kept going past the size cap."""


def make_client(settings: Settings) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        headers={"User-Agent": settings.crawler_user_agent},
        timeout=httpx2.Timeout(20.0),  # seconds to connect, and between bytes
        follow_redirects=True,
    )


async def read_limited(response: httpx2.Response, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Read a streamed body, giving up once it passes max_bytes, so a huge or endless response
    can't fill the memory."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise BodyTooLarge(f"The response is larger than {max_bytes:,} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
