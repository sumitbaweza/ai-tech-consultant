"""Screenshot descriptions: the prompt, the request, the hash cache, and the batch and direct
paths, against a fake image CDN and a fake Claude API."""

import base64
import json
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.fetch import Fetcher
from app.crawler.images import (
    MIN_BATCH,
    ScreenshotDescription,
    Setting,
    collect_batches,
    describe_new_images,
    image_type,
    request_content,
)
from app.crawler.ratelimit import HostRateLimiter
from app.crawler.robots import RobotsRules
from app.llm import load_prompt
from app.models import Image, Page, PageKind, PageStatus
from tests.fakes import batch_json, make_claude, message_json

CDN = "https://cdn.example/attachments"
DESCRIPTION = ScreenshotDescription(
    screen="Settings » Text Labels",
    summary="The list of text labels, with a search box.",
    visible_text=["Search", "Date edited"],
    settings=[Setting(name="Sort", value="Variable A-Z")],
    callouts=["1: the Search box"],
)


def png(name: str) -> bytes:
    """Bytes that start like a PNG file; `name` makes each one different."""
    return b"\x89PNG\r\n\x1a\n" + name.encode()


# --- Without a database ---------------------------------------------------------------------


def test_the_prompt_runs_on_haiku_without_thinking() -> None:
    prompt = load_prompt("describe_image", 1)

    assert (prompt.model, prompt.effort, prompt.thinking) == ("claude-haiku-5-5", "low", "disabled")


@pytest.mark.parametrize(
    ("start", "kind"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'>", None),
    ],
)
def test_image_type_comes_from_the_first_bytes(start: bytes, kind: str | None) -> None:
    assert image_type(start + b"...") == kind


def test_description_becomes_labelled_lines() -> None:
    assert DESCRIPTION.as_text() == (
        "Screen: Settings » Text Labels\n"
        "The list of text labels, with a search box.\n"
        "Visible text: Search; Date edited\n"
        "Settings: Sort = Variable A-Z\n"
        "Callouts: 1: the Search box"
    )


def test_request_holds_the_image_and_the_article_text_in_tags() -> None:
    image = Image(page_id=1, position=1, url=f"{CDN}/1.png", alt="The list", context="Open it.")

    content = request_content(png("1"), "image/png", "Text Labels", image)

    data = base64.b64encode(png("1")).decode()
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
    }
    assert content[1] == {
        "type": "text",
        "text": "<article_title>Text Labels</article_title>\n"
        "<nearby_text>Open it.</nearby_text>\n"
        "<alt_text>The list</alt_text>\n\n"
        "Describe this screenshot.",
    }


# --- With the test database -----------------------------------------------------------------


class FakeCDN:
    """Serves image bytes by URL. Anything else is a 404, including robots.txt: no rules."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = self.files.get(str(request.url))
        return httpx2.Response(200, content=body) if body is not None else httpx2.Response(404)


class FakeClaude:
    """Describes every screenshot as DESCRIPTION. A batch reports `batch_status`, and its
    results file holds `batch_results`."""

    def __init__(self) -> None:
        self.direct_calls = 0
        self.submitted: list[dict[str, Any]] = []
        self.batch_status = "in_progress"
        self.batch_results: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages":
            self.direct_calls += 1
            return httpx2.Response(200, json=message_json(DESCRIPTION.model_dump_json()))
        if request.method == "POST" and path == "/v1/messages/batches":
            self.submitted = json.loads(request.content)["requests"]
            return httpx2.Response(200, json=batch_json("in_progress"))
        if path.endswith("/results"):
            lines = "\n".join(json.dumps(line) for line in self.batch_results)
            return httpx2.Response(200, content=lines.encode())
        return httpx2.Response(200, json=batch_json(self.batch_status))


def make_fetcher(cdn: FakeCDN) -> Fetcher:
    async def no_wait(seconds: float) -> None:
        return None

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(cdn))
    limiter = HostRateLimiter(1000)
    return Fetcher(client, limiter, RobotsRules(client, "TestBot/1.0", limiter), sleep=no_wait)


async def article_with_images(session: AsyncSession, slug: str, image_urls: list[str]) -> None:
    page = Page(
        url=f"https://help.example/support/solutions/articles/{slug}",
        kind=PageKind.ARTICLE,
        status=PageStatus.ACTIVE,
        title=f"Article {slug}",
    )
    session.add(page)
    await session.flush()
    session.add_all(
        Image(page_id=page.id, position=position, url=url, context="Open Settings.")
        for position, url in enumerate(image_urls, start=1)
    )
    await session.commit()


@pytest.mark.db
async def test_new_screenshots_are_described_directly_and_each_only_once(
    db_session: AsyncSession,
) -> None:
    first, second = f"{CDN}/first.png", f"{CDN}/second.png"
    copy = f"{CDN}/copy-of-first.png"  # a different address, the same bytes as `first`
    cdn = FakeCDN({first: png("first"), second: png("second"), copy: png("first")})
    api = FakeClaude()
    claude = make_claude(api)
    await article_with_images(db_session, "1-a", [first, second])
    await article_with_images(db_session, "2-b", [copy])

    assert await describe_new_images(db_session, make_fetcher(cdn), claude)

    assert api.direct_calls == 2  # the copy shares a hash with `first`, so one request covers both
    rows = (
        await db_session.execute(select(Image.description, Image.described_by).order_by(Image.id))
    ).all()
    assert rows == [(DESCRIPTION.as_text(), "claude-haiku-5-5 describe_image@v1")] * 3

    # A later article with the same screenshot reuses the stored description
    await article_with_images(db_session, "3-c", [first])
    assert await describe_new_images(db_session, make_fetcher(cdn), claude)
    assert api.direct_calls == 2
    assert not await describe_new_images(db_session, make_fetcher(cdn), claude)  # all done


@pytest.mark.db
async def test_unusable_images_are_marked_and_not_tried_again(db_session: AsyncSession) -> None:
    missing, vector = f"{CDN}/missing.png", f"{CDN}/logo.svg"
    cdn = FakeCDN({vector: b"<svg xmlns='http://www.w3.org/2000/svg'/>"})
    api = FakeClaude()
    claude = make_claude(api)
    await article_with_images(db_session, "1-a", [missing, vector])

    await describe_new_images(db_session, make_fetcher(cdn), claude)

    errors = (await db_session.scalars(select(Image.error).order_by(Image.id))).all()
    assert errors == ["The server answered HTTP 404", "Not a JPEG, PNG, GIF or WebP image"]
    assert api.direct_calls == 0
    assert not await describe_new_images(db_session, make_fetcher(cdn), claude)


@pytest.mark.db
async def test_many_new_screenshots_go_as_one_batch(db_session: AsyncSession) -> None:
    urls = [f"{CDN}/{number}.png" for number in range(MIN_BATCH)]
    cdn = FakeCDN({url: png(url) for url in urls})
    api = FakeClaude()
    claude = make_claude(api)
    await article_with_images(db_session, "1-a", urls)

    await describe_new_images(db_session, make_fetcher(cdn), claude)

    assert (len(api.submitted), api.direct_calls) == (MIN_BATCH, 0)
    assert set((await db_session.scalars(select(Image.batch_id))).all()) == {"msgbatch_test"}
    assert not await collect_batches(db_session, claude)  # still running: nothing to store

    # The batch ends: every screenshot gets its description, except one that errored
    ids = (await db_session.scalars(select(Image.id).order_by(Image.id))).all()
    described = {"type": "succeeded", "message": message_json(DESCRIPTION.model_dump_json())}
    errored = {
        "type": "errored",
        "error": {"type": "error", "error": {"type": "invalid_request_error", "message": "Bad"}},
    }
    api.batch_status = "ended"
    api.batch_results = [{"custom_id": f"image-{ids[0]}", "result": errored}] + [
        {"custom_id": f"image-{image_id}", "result": described} for image_id in ids[1:]
    ]
    assert await collect_batches(db_session, claude)

    rows = (
        await db_session.execute(
            select(Image.description, Image.error, Image.batch_id).order_by(Image.id)
        )
    ).all()
    assert rows[0] == (None, "Batch request errored", None)
    assert rows[1:] == [(DESCRIPTION.as_text(), None, None)] * (MIN_BATCH - 1)
