"""Progress and dashboard: test 2's numbers for the whole knowledge base, and the latest run
with its progress, speed, time left and what it found. They refresh every few seconds."""

from datetime import datetime

import streamlit as st

from app.models import RunKind
from app.progress import Progress, Stats
from ui import api

REFRESH_SECONDS = 2


def render() -> None:
    st.title("Progress and dashboard")
    live()


@st.fragment(run_every=REFRESH_SECONDS)
def live() -> None:
    """Only this part runs again every REFRESH_SECONDS, not the whole page."""
    try:
        stats = api.get_brain().stats()
    except api.BrainError as error:
        st.error(str(error))
        return
    knowledge_base(stats)
    if stats.last_run is not None:
        latest_run(stats.last_run)


def knowledge_base(stats: Stats) -> None:
    # A date is too wide for a metric in a narrow window, so it's a line of text
    st.markdown(f"**Last sync:** {when(stats.last_sync)}")
    # Rows of three, so the labels fit in a narrow window too
    processed, embedded, failed = st.columns(3)
    processed.metric("Processed", f"{stats.processed:,}", help="Fetched, moved, gone or failed")
    embedded.metric("Embedded", f"{stats.embedded:,}", help="Searchable, with current chunks")
    failed.metric("Failed", f"{stats.failed:,}")
    pending, duplicates, total = st.columns(3)
    pending.metric("Pending", f"{stats.pending:,}", help="Waiting for their first fetch")
    duplicates.metric(
        "Duplicates skipped",
        f"{stats.duplicates_skipped:,}",
        help="Sitemap entries for a URL already listed: never fetched twice",
    )
    total.metric("Total pages", f"{stats.total_pages:,}")
    by_status = ", ".join(
        f"{status} {count:,}" for status, count in sorted(stats.by_status.items())
    )
    st.caption(f"Pages by status: {by_status}")


def latest_run(run: Progress) -> None:
    kind = "First crawl" if run.kind == RunKind.INITIAL else "Scheduled check"
    st.subheader(f"{kind} {run.run_id}: {run.status}")
    st.progress(
        run.done / run.total if run.total else 1.0, text=f"{run.done:,} of {run.total:,} pages"
    )
    if run.estimate is not None:
        st.caption(
            f"{run.pages_per_minute} pages a minute: about "
            f"{run.estimate.minutes_to_finish:.0f} minutes left, done around "
            f"{when(run.estimate.expected_completion)}"
        )
    found = run.summary
    counts = [
        ("New", found.new_pages),
        ("Changed", found.changed_pages),
        ("Missing", found.missing_pages),
        ("Archived", found.archived_pages),
        ("Restored", found.restored_pages),
    ]
    for column, (label, count) in zip(st.columns(len(counts)), counts, strict=True):
        column.metric(label, f"{count:,}")


def when(moment: datetime | None) -> str:
    return f"{moment:%b %d, %H:%M} UTC" if moment else "never"
