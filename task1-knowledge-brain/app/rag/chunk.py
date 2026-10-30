"""Split an article into search chunks, and make one chunk per described screenshot.

Every chunk starts with its place in the help center, such as "Settings > Text Labels >
Settings » Text Labels", plus the heading it falls under when it starts mid-section. A chunk then
makes sense on its own, and its words match questions that name the area. Text chunks break at
headings where they can, and stay between MIN_TOKENS and MAX_TOKENS, counting about
CHARS_PER_TOKEN characters per token.
"""

import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 4
MIN_TOKENS, MAX_TOKENS = 300, 500
MIN_CHARS, MAX_CHARS = MIN_TOKENS * CHARS_PER_TOKEN, MAX_TOKENS * CHARS_PER_TOKEN


@dataclass
class ChunkText:
    content: str
    token_count: int  # an estimate; Voyage reports exact totals per request in llm_calls


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def breadcrumb(category: str | None, folder: str | None, title: str | None) -> str:
    return " > ".join(part for part in (category, folder, title) if part)


def chunk_article(markdown: str, place: str) -> list[ChunkText]:
    """Pack the article's paragraphs into chunks. A heading starts a new chunk once the current
    one holds MIN_CHARS; any paragraph that would push a chunk past MAX_CHARS starts one too."""
    groups: list[tuple[str | None, list[str]]] = []  # (heading in force at the start, blocks)
    heading: str | None = None
    for block in _blocks(markdown):
        is_heading = block.startswith("#")
        size = sum(len(part) for part in groups[-1][1]) if groups else 0
        if not groups or size + len(block) > MAX_CHARS or (is_heading and size >= MIN_CHARS):
            groups.append((heading, []))
        if is_heading:
            heading = block.lstrip("#").strip()
        groups[-1][1].append(block)
    return [_make_chunk(place, start_heading, blocks) for start_heading, blocks in groups]


def image_chunk(place: str, position: int, alt: str | None, description: str) -> ChunkText:
    """One screenshot's description, named so it reads like the marker in the article text."""
    label = f"{place} > Screenshot {position}" + (f": {alt}" if alt else "")
    content = f"{label}\n\n{description}"
    return ChunkText(content, estimate_tokens(content))


def _make_chunk(place: str, heading: str | None, blocks: list[str]) -> ChunkText:
    # A chunk that starts mid-section names its section, so it still makes sense on its own
    label = f"{place} > {heading}" if heading and not blocks[0].startswith("#") else place
    content = label + "\n\n" + "\n\n".join(blocks)
    return ChunkText(content, estimate_tokens(content))


def _blocks(markdown: str) -> list[str]:
    """Paragraphs, headings and lists as written. One too long for a chunk is cut at a line
    break or sentence end."""
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", markdown):
        rest = paragraph.strip()
        while len(rest) > MAX_CHARS:
            cut = _cut_point(rest)
            blocks.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        if rest:
            blocks.append(rest)
    return blocks


def _cut_point(text: str) -> int:
    """The last line break, sentence end or space before MAX_CHARS, if it's past the halfway
    mark; otherwise MAX_CHARS itself. Either way each cut makes progress."""
    window = text[:MAX_CHARS]
    for separator in ("\n", ". ", "; ", " "):
        position = window.rfind(separator)
        if position > MAX_CHARS // 2:
            return position + len(separator)
    return MAX_CHARS
