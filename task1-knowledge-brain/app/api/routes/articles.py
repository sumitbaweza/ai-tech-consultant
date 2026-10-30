"""One article in full, with what each of its screenshots shows."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SessionDep
from app.models import Image, Page, PageKind, PageStatus

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
