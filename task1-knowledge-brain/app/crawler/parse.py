"""Turn a help-center page into clean Markdown, its place in the help center, and its images.

Built for Freshdesk help centers like this one, with fallbacks for other sites:
- The title is the article's <h1>, or else the <title> up to " | ".
- The breadcrumb gives the category ("Section: Settings" becomes "Settings") and the folder.
- The article body becomes Markdown, without the permalink line Freshdesk puts at its top.
- Each screenshot becomes a "[Screenshot N: alt text]" marker in the Markdown, plus an ImageRef
  holding its address, alt text and the text around it, which Step 6 gives Claude as context.

The content hash covers the title, the words and the image addresses. It changes when the page
really changes, and not when only whitespace or an image's cache-busting ?query does.
"""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from markdownify import markdownify

# Where the article lives, most specific first: Freshdesk's id, then generic HTML5 elements
BODY_SELECTORS = ("#article-body", "article", "main")
# Inside the body but not content: Freshdesk's permalink line, and scripts
NOT_CONTENT = ("p.the-article-link", "script", "style", "noscript")
TEXT_BLOCKS = ["p", "li", "h2", "h3", "h4", "td"]
MAX_CONTEXT_CHARS = 500


@dataclass
class ImageRef:
    position: int  # 1, 2, 3... in reading order, matching "[Screenshot N]" in the Markdown
    url: str
    alt: str | None
    context: str | None


@dataclass
class ParsedPage:
    title: str
    category: str | None
    folder: str | None
    markdown: str
    images: list[ImageRef]
    content_hash: str
    is_article: bool  # False when there's no article body, e.g. a folder page of links


def parse_page(html: bytes | str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    category, folder = _breadcrumb(soup)
    body = next((tag for selector in BODY_SELECTORS if (tag := soup.select_one(selector))), None)
    if body is None:
        title = _title(soup, None)
        return ParsedPage(title, category, folder, "", [], content_hash(title, "", []), False)

    title = _title(soup, body)
    for selector in NOT_CONTENT:
        for element in body.select(selector):
            element.decompose()
    if heading := body.select_one("h1"):
        heading.decompose()  # the title is stored on its own
    images = _swap_images_for_markers(body, url)
    markdown = _to_markdown(body)
    return ParsedPage(
        title, category, folder, markdown, images, content_hash(title, markdown, images), True
    )


def content_hash(title: str, markdown: str, images: list[ImageRef]) -> str:
    """SHA-256 of what a reader would notice changing: the title, the words and the images."""
    words = " ".join(markdown.split())  # collapse all whitespace
    parts = [title, words, *(without_query(image.url) for image in images)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def without_query(url: str) -> str:
    """Drop the ?query, such as an image's cache-buster: screenshot.png?1696295524."""
    scheme, netloc, path, _, _ = urlsplit(url)
    return urlunsplit((scheme, netloc, path, "", ""))


def _title(soup: BeautifulSoup, body: Tag | None) -> str:
    heading = body.select_one("h1") if body else None
    if heading and (text := heading.get_text(" ", strip=True)):
        return text
    if soup.title and soup.title.string:
        return soup.title.string.split(" | ")[0].strip()
    return ""


def _breadcrumb(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Freshdesk links the category to /solutions/<id> and the folder to /solutions/folders/<id>.
    Going by the link address, not its position, survives an extra "home" link."""
    category: str | None = None
    folder: str | None = None
    for link in soup.select(".breadcrumb a"):
        href = _attribute(link, "href") or ""
        text = link.get_text(" ", strip=True)
        if "/solutions/folders/" in href:
            folder = text
        elif re.search(r"/solutions/\d+", href):
            category = text.removeprefix("Section:").strip()
    return category, folder


def _swap_images_for_markers(body: Tag, page_url: str) -> list[ImageRef]:
    images: list[ImageRef] = []
    for img in body.select("img"):
        src = _attribute(img, "src")
        # The view-counter pixel (.../hit) and inline data images aren't screenshots
        if not src or src.startswith("data:") or src.rstrip("/").endswith("/hit"):
            img.decompose()
            continue
        alt = (_attribute(img, "alt") or "").strip() or None
        position = len(images) + 1
        images.append(ImageRef(position, urljoin(page_url, src), alt, _context(img, body)))
        marker = f"[Screenshot {position}: {alt}]" if alt else f"[Screenshot {position}]"
        img.replace_with(NavigableString(marker))
    return images


def _context(img: Tag, body: Tag) -> str | None:
    """The nearest text before the image, which usually says what the screenshot shows."""
    for block in img.find_all_previous(TEXT_BLOCKS):
        if body not in block.parents:
            break  # walked out of the article
        if text := block.get_text(" ", strip=True):
            return text[:MAX_CONTEXT_CHARS]
    return None


def _to_markdown(body: Tag) -> str:
    converted: str = markdownify(
        str(body),
        heading_style="ATX",
        bullets="-",
        strip=["a"],  # keep link text, drop addresses: they only add noise to search
        escape_underscores=False,
        escape_asterisks=False,
    )
    lines = [line.rstrip() for line in converted.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _attribute(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    return value if isinstance(value, str) else None
