"""A snapshot of a finished crawl, so a fresh clone answers within minutes instead of after a
polite crawl of the whole help center plus every screenshot description.

    python -m app.seed export     # write seed/brain-seed.jsonl.gz from the database
    python -m app.seed load       # load it, only if brain.pages is empty: the brain-seed service

The file is gzip-compressed JSON Lines. The first line is a header with the counts; each other
line is one row: {"table": "pages", "row": {...}}. Sitemaps come first, then pages, the rows
saying which sitemap lists which page, screenshots, and chunks, so every row's parent is loaded
before it. Each embedding is stored as float16 bytes in base64: half the size of float32, and
the rounding (about 0.05%) doesn't change which chunks are closest.

Expert answers and the expert queue are left out: they're one installation's own work, not
part of the help center. So are the crawl queue and the run history.

After a load, every sitemap's next check is one schedule interval away, as after a first crawl.
"Check now" on the Sources screen (POST /v1/sitemaps/{id}/check) fetches the help center again
at once, and only pages whose content changed are embedded again.
"""

import argparse
import asyncio
import base64
import gzip
import json
import struct
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import Column, DateTime, Table, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionFactory, engine
from app.models import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    Image,
    Page,
    PageKind,
    PageSitemap,
    PageVersion,
    Sitemap,
)

SEED_FILE = Path(__file__).resolve().parents[1] / "seed" / "brain-seed.jsonl.gz"
FORMAT_VERSION = 1
BATCH_SIZE = 500  # rows per INSERT; a chunk row has 1,024 numbers, so keep batches modest

# The columns each table keeps in the snapshot. Left out: the crawl queue and claims, run ids,
# failure counters and image batch ids, which describe work in progress, not content.
COLUMNS: dict[str, list[str]] = {
    "sitemaps": [
        "id",
        "url",
        "parent_id",
        "is_index",
        "schedule",
        "retention",
        "entry_count",
        "last_checked_at",
        "last_error",
        "created_at",
    ],
    "pages": [
        "id",
        "url",
        "kind",
        "status",
        "title",
        "category",
        "folder",
        "content_md",
        "content_hash",
        "embedded_hash",
        "http_status",
        "etag",
        "last_modified",
        "sitemap_lastmod",
        "missing_count",
        "redirected_to_id",
        "error",
        "fetched_at",
        "archived_at",
        "created_at",
    ],
    "page_sitemaps": [
        "page_id",
        "sitemap_id",
        "position",
        "first_seen_at",
        "last_seen_at",
        "removed_at",
    ],
    "images": [
        "id",
        "page_id",
        "position",
        "url",
        "alt",
        "context",
        "sha256",
        "description",
        "described_by",
        "created_at",
    ],
    "chunks": [
        "id",
        "page_id",
        "image_id",
        "kind",
        "position",
        "content",
        "token_count",
        "embedding",
        "embedding_model",
        "created_at",
    ],
}
TABLES: dict[str, Table] = {
    "sitemaps": Sitemap.__table__,  # pyright: ignore[reportAssignmentType]
    "pages": Page.__table__,  # pyright: ignore[reportAssignmentType]
    "page_sitemaps": PageSitemap.__table__,  # pyright: ignore[reportAssignmentType]
    "images": Image.__table__,  # pyright: ignore[reportAssignmentType]
    "chunks": Chunk.__table__,  # pyright: ignore[reportAssignmentType]
}


# --- Values to and from JSON --------------------------------------------------------------


def pack_vector(vector: Sequence[float]) -> str:
    """1,024 numbers as little-endian float16 bytes, in base64 text."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *vector)).decode("ascii")


def unpack_vector(packed: str) -> list[float]:
    raw = base64.b64decode(packed)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def to_json(column: str, value: object) -> object:
    if value is None:
        return None
    if column == "embedding":
        return pack_vector([float(number) for number in value])  # type: ignore[union-attr]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def from_json(column: Column[Any], value: object) -> object:
    if value is None:
        return None
    if column.name == "embedding":
        return unpack_vector(str(value))
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(str(value))
    return value


# --- Export -------------------------------------------------------------------------------


async def export(session: AsyncSession, path: Path) -> dict[str, int]:
    """Write every help-center row to `path`. Returns how many rows of each table it wrote."""
    expert_pages = select(Page.id).where(Page.kind == PageKind.EXPERT)
    queries = {
        "sitemaps": select(Sitemap).order_by(Sitemap.id),
        "pages": select(Page).where(Page.kind != PageKind.EXPERT).order_by(Page.id),
        "page_sitemaps": select(PageSitemap)
        .where(PageSitemap.page_id.not_in(expert_pages))
        .order_by(PageSitemap.page_id, PageSitemap.sitemap_id),
        "images": select(Image).where(Image.page_id.not_in(expert_pages)).order_by(Image.id),
        "chunks": select(Chunk).where(Chunk.page_id.not_in(expert_pages)).order_by(Chunk.id),
    }
    lines: list[str] = []
    counts: dict[str, int] = {}
    for table, query in queries.items():
        counts[table] = 0
        # Streamed, so the chunks' vectors never all sit in memory as Python objects at once
        async for row in await session.stream_scalars(
            query.execution_options(yield_per=BATCH_SIZE)
        ):
            values = {column: to_json(column, getattr(row, column)) for column in COLUMNS[table]}
            lines.append(json.dumps({"table": table, "row": values}, ensure_ascii=False))
            counts[table] += 1
    # End the read-only transaction, which also closes its server-side cursors
    await session.rollback()

    header = {
        "format": FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "counts": counts,
    }
    write_seed(path, header, lines)
    return counts


def write_seed(path: Path, header: dict[str, Any], lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the file identical when the data is, so git sees no change
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as file:
        file.write((json.dumps(header) + "\n").encode())
        for line in lines:
            file.write((line + "\n").encode())


# --- Load ---------------------------------------------------------------------------------


def read_header(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        header: dict[str, Any] = json.loads(file.readline())
    if header.get("format") != FORMAT_VERSION:
        raise ValueError(f"{path.name} is format {header.get('format')}, not {FORMAT_VERSION}")
    return header


def read_rows(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """The rows one at a time, as (table, row), so the file is never all in memory."""
    with gzip.open(path, "rt", encoding="utf-8") as file:
        file.readline()  # the header
        for line in file:
            item = json.loads(line)
            yield item["table"], item["row"]


async def load(session: AsyncSession, path: Path) -> dict[str, int] | None:
    """Load the snapshot into an empty knowledge base, in one transaction. Returns how many rows
    of each table it loaded, or None when the knowledge base already has pages."""
    if await session.scalar(select(func.count()).select_from(Page)):
        return None

    header = read_header(path)
    counts = dict.fromkeys(TABLES, 0)
    batch: list[dict[str, Any]] = []
    batch_table: str | None = None
    redirects: dict[int, int] = {}  # page id -> the page it moved to, set once both exist
    for table, row in read_rows(path):
        if table != batch_table:
            await _insert(session, batch_table, batch)
            batch, batch_table = [], table
        values = {
            name: from_json(TABLES[table].c[name], value)
            for name, value in row.items()
            if name in COLUMNS[table]
        }
        if table == "pages" and values.get("redirected_to_id") is not None:
            redirects[int(row["id"])] = int(row["redirected_to_id"])
            values["redirected_to_id"] = None
        batch.append(values)
        counts[table] += 1
        if len(batch) == BATCH_SIZE:
            await _insert(session, table, batch)
            batch = []
    await _insert(session, batch_table, batch)

    if counts != header["counts"]:
        raise ValueError(f"The seed file is incomplete: read {counts}, expected {header['counts']}")
    for page_id, target_id in redirects.items():
        await session.execute(
            update(Page).where(Page.id == page_id).values(redirected_to_id=target_id)
        )
    await _after_load(session)
    await session.commit()
    return counts


async def _insert(session: AsyncSession, table: str | None, rows: list[dict[str, Any]]) -> None:
    if table is not None and rows:
        await session.execute(insert(TABLES[table]), rows)


async def _after_load(session: AsyncSession) -> None:
    """What a first crawl would have left behind: each page's first version for the change
    history, sequences past the loaded ids, and each sitemap's next scheduled check."""
    await session.execute(
        insert(PageVersion).from_select(
            ["page_id", "content_hash", "title", "content_md"],
            select(Page.id, Page.content_hash, Page.title, Page.content_md).where(
                Page.content_hash.is_not(None), Page.content_md.is_not(None)
            ),
        )
    )
    # Rows were loaded with their ids, so each id sequence must move past the largest one
    for table in ("sitemaps", "pages", "images", "chunks"):
        await session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('brain.{table}', 'id'), "
                f"COALESCE((SELECT max(id) FROM brain.{table}), 1))"
            )
        )
    now = datetime.now(UTC)
    for sitemap in await session.scalars(select(Sitemap).where(Sitemap.parent_id.is_(None))):
        sitemap.next_check_at = now + sitemap.schedule.interval


# --- Command line -------------------------------------------------------------------------


async def _run(action: str, path: Path) -> dict[str, int] | None:
    try:
        async with SessionFactory() as session:
            if action == "export":
                return await export(session, path)
            return await load(session, path)
    finally:
        await engine.dispose()  # close the pooled connections before the event loop closes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed", description="Export or load the crawl snapshot"
    )
    parser.add_argument("action", choices=["export", "load"])
    parser.add_argument("--path", type=Path, default=SEED_FILE)
    args = parser.parse_args(argv)
    path: Path = args.path

    if args.action == "load" and not path.is_file():
        print(f"No {path}: starting with an empty knowledge base")
        return 0
    counts = asyncio.run(_run(args.action, path))
    if args.action == "export":
        print(f"Wrote {path.name} ({path.stat().st_size / 1_000_000:.1f} MB): {counts}")
    elif counts is None:
        print("The knowledge base already has pages, so the seed wasn't loaded")
    else:
        print(f"Loaded {path.name}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
