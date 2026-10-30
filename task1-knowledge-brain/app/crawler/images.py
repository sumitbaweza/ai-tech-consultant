"""Describe every screenshot with Claude Haiku, so that a question only a screenshot answers can
still be found and cited.

One round of describe_new_images():
1. Takes up to ROUND_SIZE images of active pages that have no description yet.
2. Downloads each one politely and hashes its bytes. A screenshot with the same hash, already
   described by the same prompt version, reuses that description: it's never paid for twice.
3. Sends the rest to Claude, one request per distinct screenshot. With at least MIN_BATCH of
   them, that's one Message Batch: half price, finished within 24 hours. Fewer go as direct
   calls, which take seconds. A batch's id is stored on its image rows, so a restart doesn't
   lose it.

collect_batches() reads each finished batch and stores its descriptions.
"""

import base64
import hashlib
import logging
from collections import defaultdict
from typing import Literal

from anthropic.types.beta import BetaContentBlockParam
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.fetch import Fetcher, FetchError
from app.llm import Claude, LLMError, load_prompt
from app.models import Image, Page, PageStatus

logger = logging.getLogger("brain.images")

PROMPT, VERSION = "describe_image", 1
ROUND_SIZE = 200  # images per round: at 2 downloads a second, about 2 minutes of downloading
MIN_BATCH = 20  # fewer new screenshots than this are described directly, in seconds
MAX_IMAGE_BYTES = 3_750_000  # Claude takes images up to 5 MB, and base64 adds a third

ImageType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]


class ImageError(Exception):
    """The image couldn't be used. Stored on the row, so it isn't tried again and again."""


class Setting(BaseModel):
    name: str
    value: str


class ScreenshotDescription(BaseModel):
    """What Haiku returns for one screenshot (prompts/describe_image)."""

    screen: str
    summary: str
    visible_text: list[str]
    settings: list[Setting]
    callouts: list[str]

    def as_text(self) -> str:
        """The description as stored, embedded and shown: one labelled line per part."""
        lines = [f"Screen: {self.screen}", self.summary]
        if self.visible_text:
            lines.append("Visible text: " + "; ".join(self.visible_text))
        if self.settings:
            values = "; ".join(f"{setting.name} = {setting.value}" for setting in self.settings)
            lines.append(f"Settings: {values}")
        if self.callouts:
            lines.append("Callouts: " + "; ".join(self.callouts))
        return "\n".join(lines)


def image_type(data: bytes) -> ImageType | None:
    """The image format, read from the file's first bytes. These are the four Claude reads."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def request_content(
    data: bytes, kind: ImageType, title: str | None, image: Image
) -> list[BetaContentBlockParam]:
    """The screenshot, plus what the article says around it, in tags that mark it as data."""
    context = [
        f"<article_title>{title}</article_title>" if title else "",
        f"<nearby_text>{image.context}</nearby_text>" if image.context else "",
        f"<alt_text>{image.alt}</alt_text>" if image.alt else "",
    ]
    text = "\n".join(part for part in context if part)
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": kind,
                "data": base64.b64encode(data).decode(),
            },
        },
        {"type": "text", "text": f"{text}\n\nDescribe this screenshot."},
    ]


async def download(fetcher: Fetcher, url: str) -> tuple[bytes, ImageType]:
    try:
        fetched = await fetcher.get(url)
    except FetchError as error:
        raise ImageError(f"Couldn't download it: {error}") from error
    if fetched.status != 200:
        raise ImageError(f"The server answered HTTP {fetched.status}")
    kind = image_type(fetched.body)
    if kind is None:
        raise ImageError("Not a JPEG, PNG, GIF or WebP image")
    if len(fetched.body) > MAX_IMAGE_BYTES:
        raise ImageError(f"Larger than {MAX_IMAGE_BYTES:,} bytes")
    return fetched.body, kind


async def describe_new_images(session: AsyncSession, fetcher: Fetcher, claude: Claude) -> bool:
    """One round, as described at the top. Returns False when there was nothing to do."""
    described_by = _described_by()
    rows = (
        await session.execute(
            select(Image, Page.title)
            .join(Page, Page.id == Image.page_id)
            .where(
                Page.status == PageStatus.ACTIVE,
                Image.description.is_(None),
                Image.batch_id.is_(None),
                Image.error.is_(None),
            )
            .order_by(Image.id)
            .limit(ROUND_SIZE)
        )
    ).all()
    if not rows:
        return False

    # One request per distinct screenshot, and every row that request will describe
    requests: dict[str, list[BetaContentBlockParam]] = {}
    waiting: dict[str, list[Image]] = defaultdict(list)
    for image, title in rows:
        try:
            data, kind = await download(fetcher, image.url)
        except ImageError as error:
            image.error = str(error)
            continue
        sha256 = image.sha256 = hashlib.sha256(data).hexdigest()
        known = await session.scalar(
            select(Image.description)
            .where(Image.sha256 == sha256, Image.described_by == described_by)
            .where(Image.description.is_not(None))
            .limit(1)
        )
        if known is not None:
            image.description, image.described_by = known, described_by
            continue
        if sha256 not in requests:
            requests[sha256] = request_content(data, kind, title, image)
        waiting[sha256].append(image)
    # Keep the hashes, errors and reused descriptions, whatever happens next
    await session.commit()

    if len(requests) >= MIN_BATCH:
        batch_id = await claude.submit_batch(
            [
                claude.batch_request(
                    f"image-{waiting[sha256][0].id}",
                    PROMPT,
                    VERSION,
                    content,
                    ScreenshotDescription,
                )
                for sha256, content in requests.items()
            ]
        )
        for images in waiting.values():
            for image in images:
                image.batch_id = batch_id
        logger.info("Sent %d screenshots to Claude as batch %s", len(requests), batch_id)
    else:
        for sha256, content in requests.items():
            try:
                described = await claude.call(PROMPT, VERSION, content, ScreenshotDescription)
            except LLMError as error:
                for image in waiting[sha256]:
                    image.error = str(error)
            else:
                for image in waiting[sha256]:
                    image.description, image.described_by = described.as_text(), described_by
    await session.commit()
    return True


async def collect_batches(session: AsyncSession, claude: Claude) -> bool:
    """Store the descriptions from every finished batch. Returns True if any batch finished."""
    described_by = _described_by()
    batch_ids = (
        await session.scalars(select(Image.batch_id).where(Image.batch_id.is_not(None)).distinct())
    ).all()
    finished = False
    for batch_id in batch_ids:
        if batch_id is None:
            continue
        results = await claude.batch_results(batch_id, PROMPT, VERSION, ScreenshotDescription)
        if results is None:
            continue  # still running
        for custom_id, result in results.items():
            image_id = int(custom_id.removeprefix("image-"))
            sha256 = await session.scalar(select(Image.sha256).where(Image.id == image_id))
            if sha256 is None:
                continue  # the row was replaced while the batch ran
            # The same screenshot may sit on several rows of this batch: fill them all
            same_screenshot = update(Image).where(
                Image.batch_id == batch_id, Image.sha256 == sha256
            )
            if isinstance(result, LLMError):
                await session.execute(same_screenshot.values(error=str(result), batch_id=None))
            else:
                await session.execute(
                    same_screenshot.values(
                        description=result.as_text(), described_by=described_by, batch_id=None
                    )
                )
        # A row the batch returned nothing for goes back in line for the next round
        await session.execute(update(Image).where(Image.batch_id == batch_id).values(batch_id=None))
        await session.commit()
        logger.info("Stored the results of batch %s", batch_id)
        finished = True
    return finished


def _described_by() -> str:
    """Model and prompt version, e.g. "claude-haiku-5-5 describe_image@v1". A cached
    description is reused only when this matches, so a new prompt version describes again."""
    prompt = load_prompt(PROMPT, VERSION)
    return f"{prompt.model} {prompt.label}"
