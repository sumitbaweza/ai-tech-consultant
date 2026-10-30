"""Change history and archive: the pages out of search, and what changed in any article."""

import streamlit as st

from app.models import PageStatus
from ui import api
from ui.common import table, when

# The lists of pages out of search: each status, and what it means
LISTS = [
    ("Archived", PageStatus.ARCHIVED, "Missing from the sitemap past the retention rule: kept"),
    ("Not found", PageStatus.NOT_FOUND, "Answered 404 or 410 when last fetched"),
    ("Moved", PageStatus.REDIRECTED, "Answered 301: the new address is crawled instead"),
    ("Failed", PageStatus.FAILED, "Couldn't be fetched, after several tries"),
]


def render() -> None:
    st.title("Change history and archive")
    tabs = st.tabs([label for label, _, _ in LISTS])
    for tab, (_, status, meaning) in zip(tabs, LISTS, strict=True):
        with tab:
            st.caption(meaning)
            page_list(status)

    st.subheader("What changed in an article")
    article_id = st.number_input(
        "Article id",
        min_value=1,
        step=1,
        value=None,
        placeholder="From a list above, a citation or a search hit",
    )
    if article_id is not None:
        show_history(int(article_id))


def page_list(status: PageStatus) -> None:
    try:
        pages = api.get_brain().articles(status)
    except api.BrainError as error:
        st.error(str(error))
        return
    if not pages.items:
        st.caption("None")
        return
    table(
        [
            {
                "Id": page.id,
                "Title": page.title,
                "URL": page.url,
                "Checks missed": page.missing_count,
                "Archived": when(page.archived_at) if page.archived_at else "",
                "Last error": page.error,
            }
            for page in pages.items
        ]
    )
    if pages.total > len(pages.items):
        st.caption(f"The first {len(pages.items):,} of {pages.total:,}")


def show_history(article_id: int) -> None:
    try:
        history = api.get_brain().history(article_id)
    except api.BrainError as error:
        st.error(str(error))
        return
    st.markdown(f"**{history.title or history.url}** is {history.status}.")
    for version in history.versions:  # newest first
        st.markdown(f"**Version of {when(version.created_at)}**")
        if version.diff is None:
            st.caption("The first version: nothing to compare it with")
        elif version.diff:
            st.code(version.diff, language="diff")
        else:
            st.caption("The text is the same as the version before")
