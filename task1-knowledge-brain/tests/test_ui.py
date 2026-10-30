"""The screens: password sign-in, and the dashboard against a fake Brain API, run by
Streamlit's own test harness."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from streamlit.testing.v1 import AppTest

from app.config import get_settings
from ui import api
from ui.passwords import check_password, hash_password, sign_in

MAIN = str(Path(__file__).resolve().parents[1] / "ui" / "main.py")

STATS: dict[str, Any] = {
    "total_pages": 1700,
    "processed": 1700,
    "embedded": 1500,
    "failed": 0,
    "pending": 0,
    "duplicates_skipped": 0,
    "archived": 0,
    "last_sync": "2026-10-30T13:14:32Z",
    "by_status": {"active": 1700},
    "last_run": {
        "run_id": 2,
        "kind": "refresh",
        "status": "running",
        "total": 1700,
        "done": 850,
        "remaining": 850,
        "by_status": {"active": 1700},
        "pages_per_minute": 59.1,
        "estimate": {
            "pages_per_minute": 59.1,
            "pages_per_hour": 3546,
            "pages_per_day": 85104,
            "minutes_to_finish": 14.4,
            "expected_completion": "2026-10-30T13:29:00Z",
        },
        "summary": {
            "new_pages": 0,
            "changed_pages": 3,
            "missing_pages": 1,
            "archived_pages": 0,
            "restored_pages": 0,
        },
        "started_at": "2026-10-30T12:45:55Z",
        "finished_at": None,
    },
}


@pytest.fixture
def sam(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """One user who may sign in: sam, whose password is "right horse"."""
    monkeypatch.setenv("BRAIN_UI_USERS", f"sam:{hash_password('right horse')}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_brain(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Point the screens at a fake API that answers /v1/stats. Returns the paths requested."""
    requested: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requested.append(request.url.path)
        return httpx2.Response(200, json=STATS)

    client = httpx2.Client(transport=httpx2.MockTransport(handle), base_url="http://brain.test")
    monkeypatch.setattr(api, "get_brain", lambda: api.Brain(client))
    return requested


def test_a_password_matches_only_its_own_hash() -> None:
    stored = hash_password("right horse")

    assert check_password("right horse", stored)
    assert not check_password("wrong horse", stored)
    assert hash_password("right horse") != stored  # a fresh salt every time


def test_sign_in_needs_a_known_name_and_its_password(sam: None) -> None:
    assert sign_in("sam", "right horse")
    assert not sign_in("sam", "wrong horse")
    assert not sign_in("ann", "right horse")


def test_no_pages_folder_lists_the_screens_before_sign_in() -> None:
    # Streamlit would turn a `pages` folder next to ui/main.py into sidebar links of its own,
    # outside the sign-in
    assert not (Path(MAIN).parent / "pages").exists()


def test_nothing_shows_before_signing_in(sam: None, fake_brain: list[str]) -> None:
    app = AppTest.from_file(MAIN, default_timeout=10).run()
    app.text_input[0].input("sam")
    app.text_input[1].input("wrong horse")
    app.button[0].click().run()

    assert app.error[0].value == "Wrong name or password"
    assert fake_brain == []  # the API was never called


def test_signed_in_the_dashboard_shows_the_numbers_and_the_latest_run(
    sam: None, fake_brain: list[str]
) -> None:
    app = AppTest.from_file(MAIN, default_timeout=10).run()
    app.text_input[0].input("sam")
    app.text_input[1].input("right horse")
    app.button[0].click().run()

    metrics = {metric.label: metric.value for metric in app.metric}
    assert (metrics["Embedded"], metrics["Total pages"]) == ("1,500", "1,700")
    assert (metrics["Changed"], metrics["Missing"]) == ("3", "1")
    assert "**Last sync:** Oct 30, 13:14 UTC" in [markdown.value for markdown in app.markdown]
    assert app.subheader[0].value == "Scheduled check 2: running"
    assert fake_brain == ["/v1/stats"]
