"""Parsing a help-center article into Markdown, its breadcrumb, screenshots and content hash."""

from collections.abc import Callable
from pathlib import Path

from app.crawler.parse import ParsedPage, parse_page

FIXTURE = Path(__file__).parent / "fixtures" / "freshdesk_article.html"
URL = "https://help.example/support/solutions/articles/1-example-labels"


def parse_fixture(change: Callable[[str], str] = lambda html: html) -> ParsedPage:
    return parse_page(change(FIXTURE.read_text(encoding="utf-8")), URL)


def test_title_and_place_in_the_help_center() -> None:
    page = parse_fixture()

    assert page.title == "Settings » Example Labels"
    assert (page.category, page.folder) == ("Settings", "Example Labels")
    assert page.is_article


def test_body_becomes_markdown_without_freshdesk_chrome() -> None:
    markdown = parse_fixture().markdown

    assert markdown.startswith("## Overview")
    assert "### Edit a label" in markdown
    assert "1. Open **Settings » Text Labels**." in markdown
    assert "Click Edit next to the label." in markdown  # link text kept, its address dropped
    for leftover in ("Link:", "trackView", "Solution home", "/hit", "# Settings » Example Labels"):
        assert leftover not in markdown


def test_screenshots_become_markers_with_alt_text_and_context() -> None:
    page = parse_fixture()

    assert [(image.position, image.url, image.alt, image.context) for image in page.images] == [
        (
            1,
            "https://help.example/helpdesk/attachments/111/labels-list.png?1700000000",
            "The label list with a search box",
            "Labels change the words visitors see on the site.",
        ),
        (2, "https://cdn.example/attachments/222/edit-window.png", None, "The edit window opens:"),
    ]
    assert "[Screenshot 1: The label list with a search box]" in page.markdown
    assert "[Screenshot 2]" in page.markdown


def hash_after(old: str, new: str) -> str:
    return parse_fixture(lambda html: html.replace(old, new)).content_hash


def test_hash_changes_with_the_content_only() -> None:
    original = parse_fixture().content_hash

    # The same content: extra whitespace, or a new cache-buster on a screenshot
    assert hash_after("visitors see", "visitors   see") == original
    assert hash_after("?1700000000", "?1800000000") == original
    # Real changes: different words, or a different screenshot
    assert hash_after("visitors see", "members see") != original
    assert hash_after("111/labels", "333/labels") != original


def test_page_without_an_article_body_is_a_listing() -> None:
    html = (
        "<html><head><title>Emails | Example Help Center</title></head>"
        "<body><ul><li><a href='/support/solutions/articles/1'>Email Accounts</a></li></ul>"
        "</body></html>"
    )

    page = parse_page(html, "https://help.example/support/solutions/folders/12")

    assert (page.is_article, page.title, page.markdown, page.images) == (False, "Emails", "", [])
