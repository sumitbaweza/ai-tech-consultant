"""The knowledge base's pages: one in full with what each screenshot shows, its change history,
and lists by status, such as the archived knowledge base."""

import difflib
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, func, select

from app.api.deps import SessionDep
from app.models import Image, Page, PageKind, PageStatus, PageVersion

router = APIRouter(prefix="/articles", tags=["articles"])


class ArticleImage(BaseModel):
    position: int
    url: str
    alt: str | None
    description: str | None  # None until Claude has described it


class Article(BaseModel):
    id: int
    url: str
    kind: PageKind
    status: PageStatus  # archived articles are returned too, so old citations still resolve
    title: str | None
    category: str | None
    folder: str | None
    content_md: str | None
    fetched_at: datetime | None
    images: list[ArticleImage]


class ArticleSummary(BaseModel):
    """One line of a list: enough to pick a page and see why it's in that list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    kind: PageKind
    status: PageStatus
    title: str | None
    missing_count: int  # sitemap checks in a row that didn't list it
    fetched_at: datetime | None
    archived_at: datetime | None
    error: str | None  # why the last fetch failed


class ArticleList(BaseModel):
    total: int  # every page that matches; `items` is one page of them
    items: list[ArticleSummary]


class ArticleVersion(BaseModel):
    id: int
    created_at: datetime
    title: str | None
    content_hash: str
    # What changed since the version before, as a unified diff of the title and the Markdown.
    # None for the first version.
    diff: str | None


class ArticleHistory(BaseModel):
    id: int
    url: str
    title: str | None
    status: PageStatus
    missing_count: int
    archived_at: datetime | None
    versions: list[ArticleVersion]  # newest first


@router.get("")
async def list_articles(
    session: SessionDep,
    page_status: Annotated[
        PageStatus | None,
        Query(alias="status", description="Only pages with this status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ArticleList:
    """Pages in the knowledge base, optionally with one status: `archived` lists the archived
    knowledge base, and `failed` or `not_found` the pages the dashboard counts as such. The
    most recently archived come first, then the rest in the order they were found."""
    where: list[ColumnElement[bool]] = [] if page_status is None else [Page.status == page_status]
    total = await session.scalar(select(func.count()).select_from(Page).where(*where))
    # Only the columns a line needs, not each page's full text
    rows = await session.execute(
        select(
            Page.id,
            Page.url,
            Page.kind,
            Page.status,
            Page.title,
            Page.missing_count,
            Page.fetched_at,
            Page.archived_at,
            Page.error,
        )
        .where(*where)
        .order_by(Page.archived_at.desc().nulls_last(), Page.id)
        .limit(limit)
        .offset(offset)
    )
    return ArticleList(total=total or 0, items=[ArticleSummary.model_validate(row) for row in rows])


@router.get("/{article_id}")
async def get_article(article_id: int, session: SessionDep) -> Article:
    """The article's text as Markdown, where each screenshot appears as "[Screenshot N]",
    and the screenshots themselves with their descriptions."""
    page = await session.get(Page, article_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No article with id {article_id}")
    images = await session.scalars(
        select(Image).where(Image.page_id == page.id).order_by(Image.position)
    )
    return Article(
        id=page.id,
        url=page.url,
        kind=page.kind,
        status=page.status,
        title=page.title,
        category=page.category,
        folder=page.folder,
        content_md=page.content_md,
        fetched_at=page.fetched_at,
        images=[
            ArticleImage(
                position=image.position,
                url=image.url,
                alt=image.alt,
                description=image.description,
            )
            for image in images
        ],
    )


@router.get("/{article_id}/history")
async def article_history(
    article_id: int, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> ArticleHistory:
    """The change history: a version each time the article's content changed, newest first,
    each with a diff from the version before. An archived article keeps its history."""
    page = await session.get(Page, article_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No article with id {article_id}")
    # One more than shown: the oldest version shown needs the one before it for its diff
    rows = (
        await session.scalars(
            select(PageVersion)
            .where(PageVersion.page_id == page.id)
            .order_by(PageVersion.id.desc())
            .limit(limit + 1)
        )
    ).all()
    versions: list[ArticleVersion] = []
    for position, version in enumerate(rows[:limit]):
        before = rows[position + 1] if position + 1 < len(rows) else None
        versions.append(
            ArticleVersion(
                id=version.id,
                created_at=version.created_at,
                title=version.title,
                content_hash=version.content_hash,
                diff=_diff(before, version) if before is not None else None,
            )
        )
    return ArticleHistory(
        id=page.id,
        url=page.url,
        title=page.title,
        status=page.status,
        missing_count=page.missing_count,
        archived_at=page.archived_at,
        versions=versions,
    )


def _diff(before: PageVersion, after: PageVersion) -> str:
    """A unified diff, with two lines of context around each change."""
    return "\n".join(
        difflib.unified_diff(
            _lines(before),
            _lines(after),
            fromfile=before.created_at.isoformat(timespec="seconds"),
            tofile=after.created_at.isoformat(timespec="seconds"),
            n=2,
            lineterm="",
        )
    )


def _lines(version: PageVersion) -> list[str]:
    """The title as a heading, then the Markdown, so a new title shows in the diff too."""
    return [f"# {version.title or ''}", "", *version.content_md.splitlines()]
