from app.rag.chunk import MAX_CHARS, breadcrumb, chunk_article, image_chunk

PLACE = "Settings > Text Labels > Settings » Text Labels"


def words(count: int) -> str:
    """A paragraph of about 6 characters per word."""
    return " ".join(["label"] * count)


def test_breadcrumb_leaves_out_missing_parts() -> None:
    assert breadcrumb("Settings", None, "Text Labels") == "Settings > Text Labels"


def test_a_short_article_is_one_chunk_that_starts_with_its_place() -> None:
    [chunk] = chunk_article("## Overview\n\nLabels change words.", PLACE)

    assert chunk.content == f"{PLACE}\n\n## Overview\n\nLabels change words."
    assert chunk.token_count == round(len(chunk.content) / 4)


def test_small_sections_are_packed_together() -> None:
    body = "\n\n".join(["## One", "Short.", "## Two", "Also short."])

    assert len(chunk_article(body, PLACE)) == 1


def test_a_heading_starts_a_new_chunk_once_the_chunk_is_big_enough() -> None:
    body = "\n\n".join(["## One", words(250), "## Two", words(50)])  # 1,500 chars, then more

    chunks = chunk_article(body, PLACE)

    assert [chunk.content.split("\n\n")[1] for chunk in chunks] == ["## One", "## Two"]


def test_a_chunk_that_starts_mid_section_names_its_section() -> None:
    body = "\n\n".join(["## Using labels", words(300), words(300)])  # two 1,800-char paragraphs

    chunks = chunk_article(body, PLACE)

    assert len(chunks) == 2
    assert chunks[1].content.startswith(f"{PLACE} > Using labels\n\n")


def test_a_huge_paragraph_is_cut_so_no_chunk_is_over_the_limit() -> None:
    chunks = chunk_article(words(2000) + ".", PLACE)  # 12,000 characters in one paragraph

    assert len(chunks) > 5
    assert all(len(chunk.content) <= MAX_CHARS + len(PLACE) + 2 for chunk in chunks)


def test_a_screenshot_chunk_reads_like_its_marker_in_the_article() -> None:
    chunk = image_chunk(PLACE, 2, "The sort menu", "Screen: Settings » Text Labels")

    assert chunk.content == (
        f"{PLACE} > Screenshot 2: The sort menu\n\nScreen: Settings » Text Labels"
    )


def test_an_empty_article_has_no_chunks() -> None:
    assert chunk_article("", PLACE) == []
