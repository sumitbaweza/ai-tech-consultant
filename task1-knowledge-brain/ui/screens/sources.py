"""Sources: add sitemaps, see what they hold before anything is crawled, confirm the crawl,
and check a sitemap again without waiting for its schedule."""

import streamlit as st

from app.ingest import Preview
from app.models import Retention, Schedule
from ui import api
from ui.common import flash, show_flash, table, when

PREVIEW = "sources_preview"  # session state: the preview waiting to be confirmed

SCHEDULES = {
    Schedule.DAILY: "Daily",
    Schedule.WEEKLY: "Weekly",
    Schedule.EVERY_15_DAYS: "Every 15 days",
    Schedule.EVERY_30_DAYS: "Every 30 days",
}
RETENTIONS = {
    Retention.IMMEDIATE: "As soon as a check misses it",
    Retention.AFTER_2: "After 2 checks in a row miss it",
    Retention.AFTER_15: "After 15 checks in a row miss it",
    Retention.AFTER_30: "After 30 checks in a row miss it",
}


def render() -> None:
    st.title("Sources")
    show_flash()
    preview: Preview | None = st.session_state.get(PREVIEW)
    if preview is None:
        add_form()
    else:
        show_preview(preview)
    st.divider()
    your_sitemaps()


def add_form() -> None:
    with st.form("add_sitemaps"):
        urls = st.text_area(
            "Sitemap URLs, one per line",
            placeholder="https://support.brilliantdirectories.com/support/sitemap.xml",
        )
        left, right = st.columns(2)
        schedule = left.selectbox(
            "Check them again", list(SCHEDULES), format_func=SCHEDULES.__getitem__
        )
        retention = right.selectbox(
            "Archive a URL they drop",
            list(RETENTIONS),
            index=1,
            format_func=RETENTIONS.__getitem__,
        )
        submitted = st.form_submit_button("Read the sitemaps", type="primary")
    if not submitted:
        return
    lines = [line.strip() for line in urls.splitlines() if line.strip()]
    if not lines:
        st.error("Add at least one sitemap URL")
        return
    try:
        with st.spinner("Reading the sitemaps, and any sitemaps inside them"):
            st.session_state[PREVIEW] = api.get_brain().add_sitemaps(lines, schedule, retention)
    except api.BrainError as error:
        st.error(str(error))
        return
    st.rerun()


def show_preview(preview: Preview) -> None:
    """Test 2's summary before processing: per sitemap, and the totals."""
    st.subheader("Before anything is crawled")
    sitemaps, urls, unique, new = st.columns(4)
    sitemaps.metric("Sitemaps", f"{preview.total_sitemaps:,}")
    urls.metric("URLs", f"{preview.total_urls:,}")
    unique.metric("Unique URLs", f"{preview.unique_urls:,}")
    new.metric("New to fetch", f"{preview.new_urls:,}")
    table(
        [
            {
                "Sitemap": sitemap.url,
                "Entries": sitemap.entries,
                "First URL": sitemap.first_url,
                "Last URL": sitemap.last_url,
                "Problem": sitemap.error,
            }
            for sitemap in preview.sitemaps
        ]
    )
    if preview.duplicate_urls:
        st.markdown(f"**Duplicate URLs: {preview.duplicate_urls:,}**, each fetched only once.")
    if preview.duplicates:
        table(
            [
                {"URL": duplicate.url, "Listed in": ", ".join(duplicate.sitemaps)}
                for duplicate in preview.duplicates
            ]
        )
    estimate = preview.estimate
    rate = (
        f"At the polite rate: {estimate.pages_per_minute:g} pages a minute, "
        f"{estimate.pages_per_hour:,.0f} an hour, {estimate.pages_per_day:,.0f} a day."
    )
    if preview.new_urls:
        st.caption(
            f"{rate} The {preview.new_urls:,} new pages take about "
            f"{estimate.minutes_to_finish:,.0f} minutes: done around "
            f"{when(estimate.expected_completion)}."
        )
    else:
        st.caption(f"{rate} Nothing new to fetch: every URL is already in the knowledge base.")

    confirm, discard = st.columns(2)
    if confirm.button("Confirm and start the crawl", type="primary"):
        try:
            run = api.get_brain().confirm(preview.sitemap_ids)
        except api.BrainError as error:
            st.error(str(error))
            return
        del st.session_state[PREVIEW]
        if run.total_pages:
            flash(f"Crawl {run.id} started: {run.total_pages:,} new pages queued.")
        else:
            flash("Nothing new to fetch: every URL is already in the knowledge base.")
        st.rerun()
    if discard.button("Not now", help="The sitemaps stay below, unconfirmed"):
        del st.session_state[PREVIEW]
        st.rerun()


def your_sitemaps() -> None:
    st.subheader("Your sitemaps")
    try:
        sitemaps = api.get_brain().sitemaps()
    except api.BrainError as error:
        st.error(str(error))
        return
    if not sitemaps:
        st.caption("None yet")
    for sitemap in sitemaps:
        confirmed = sitemap.next_check_at is not None
        next_check = when(sitemap.next_check_at) if confirmed else "once you confirm its crawl"
        with st.container(border=True):
            st.markdown(f"**{sitemap.url}**")
            st.caption(
                f"Checked {SCHEDULES[sitemap.schedule].lower()}. A URL it drops is archived "
                f"{RETENTIONS[sitemap.retention].lower()}. Last check: "
                f"{when(sitemap.last_checked_at)}. Next: {next_check}."
            )
            if sitemap.last_error:
                st.warning(sitemap.last_error)
            if not confirmed:
                if st.button("Preview and confirm", key=f"preview_{sitemap.id}"):
                    load_preview(sitemap.id)
            elif st.button("Check now", key=f"check_{sitemap.id}"):
                check_now(sitemap.id)


def load_preview(sitemap_id: int) -> None:
    try:
        st.session_state[PREVIEW] = api.get_brain().preview(sitemap_id)
    except api.BrainError as error:
        flash(str(error), "error")
    st.rerun()


def check_now(sitemap_id: int) -> None:
    try:
        api.get_brain().check_now(sitemap_id)
    except api.BrainError as error:
        flash(str(error), "error")
    else:
        flash(
            "Checking it within a minute: new URLs are added, and every page is fetched again. "
            "The dashboard follows it."
        )
    st.rerun()
