"""The screens: password sign-in, the dashboard, and each other screen against a fake Brain
API, run by Streamlit's own test harness."""

import json
from collections.abc import Callable, Iterator
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


# --- Each screen on its own, signed in, against a fake API --------------------------------------

SITEMAP = "https://help.example/sitemap.xml"
ARTICLE = "https://help.example/support/solutions/articles/1-text-labels"
WHEN = "2026-10-30T15:00:00Z"
ANSWER = "Open Settings » Text Labels."

PREVIEW: dict[str, Any] = {
    "sitemap_ids": [1],
    "sitemaps": [
        {
            "id": 1,
            "url": SITEMAP,
            "parent_id": None,
            "is_index": False,
            "entries": 3,
            "first_url": ARTICLE,
            "last_url": f"{ARTICLE}-2",
            "error": None,
        }
    ],
    "total_sitemaps": 1,
    "total_urls": 3,
    "unique_urls": 2,
    "duplicate_urls": 1,
    "duplicates": [],
    "new_urls": 2,
    "estimate": {
        "pages_per_minute": 60.0,
        "pages_per_hour": 3600.0,
        "pages_per_day": 86400.0,
        "minutes_to_finish": 0.0,
        "expected_completion": WHEN,
    },
}
SITEMAP_STATE: dict[str, Any] = {
    "id": 1,
    "url": SITEMAP,
    "schedule": "daily",
    "retention": "after_2",
    "last_checked_at": WHEN,
    "next_check_at": WHEN,
    "last_error": None,
}
TASK: dict[str, Any] = {
    "id": 3,
    "question": "What's the weather in Berlin?",
    "history": [],
    "reason": "low_retrieval",
    "status": "open",
    "asked_count": 2,
    "created_at": WHEN,
    "last_asked_at": WHEN,
    "given_answer": None,
    "retrieved": [],
    "answer": None,
    "answered_by": None,
    "answered_at": None,
    "answer_page_id": None,
}


def answer_json(answered: bool) -> dict[str, Any]:
    """What /v1/ask returns: an answer citing the Text Labels article, or an honest no."""
    citation = {
        "source_id": "S1",
        "origin": "article",
        "chunk_id": 1,
        "page_id": 217,
        "title": "Text Labels",
        "url": ARTICLE,
        "image_url": None,
        "snippet": "Text Labels let you rename the words visitors see.",
    }
    return {
        "question": "q",
        "standalone_question": "How do I rename words?",
        "answered": answered,
        "answer": ANSWER if answered else "I couldn't find this.",
        "citations": [citation] if answered else [],
        "confidence": 0.9 if answered else 0.0,
        "outcome": "answered" if answered else "low_retrieval",
        "sources": [],
        "expert_task_id": None if answered else 4,
    }


class FakeApi:
    """Answers the screens' calls by method and path with canned JSON, or with what a function
    of the request returns, and records each call."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        reply = self.routes.get((request.method, request.url.path))
        if reply is None:
            return httpx2.Response(404, json={"detail": "Not faked"})
        return httpx2.Response(200, json=reply(request) if callable(reply) else reply)


type UseApi = Callable[[dict[tuple[str, str], Any]], FakeApi]


@pytest.fixture
def use_api(monkeypatch: pytest.MonkeyPatch) -> UseApi:
    def use(routes: dict[tuple[str, str], Any]) -> FakeApi:
        fake = FakeApi(routes)
        client = httpx2.Client(transport=httpx2.MockTransport(fake), base_url="http://brain.test")
        monkeypatch.setattr(api, "get_brain", lambda: api.Brain(client))
        return fake

    return use


def run_screen(name: str) -> None:
    """Runs as the app script: one screen, the way ui/main.py runs it after the sign-in."""
    import importlib

    importlib.import_module(f"ui.screens.{name}").render()


def screen(name: str) -> AppTest:
    app = AppTest.from_function(run_screen, kwargs={"name": name}, default_timeout=10)
    app.session_state["user"] = "sam"
    return app.run()


def click(app: AppTest, label: str) -> None:
    [button] = [button for button in app.button if button.label == label]
    button.click().run()


def texts(elements: Any) -> list[str]:
    return [str(element.value) for element in elements]


def test_sources_reads_the_sitemaps_then_confirms_the_crawl(use_api: UseApi) -> None:
    ingestion = {
        "id": 7,
        "kind": "initial",
        "status": "running",
        "sitemap_ids": [1],
        "total_pages": 2,
        "started_at": WHEN,
    }
    fake = use_api(
        {
            ("POST", "/v1/sitemaps"): PREVIEW,
            ("GET", "/v1/sitemaps"): [],
            ("POST", "/v1/ingestions"): ingestion,
        }
    )
    app = screen("sources")

    app.text_area[0].input(SITEMAP)
    click(app, "Read the sitemaps")
    metrics = {metric.label: metric.value for metric in app.metric}
    preview_captions = [caption for caption in app.caption if "polite rate" in caption.value]
    click(app, "Confirm and start the crawl")

    assert (metrics["URLs"], metrics["New to fetch"]) == ("3", "2")
    assert "The 2 new pages take about 0 minutes" in texts(preview_captions)[0]
    expected = {"urls": [SITEMAP], "schedule": "daily", "retention": "after_2"}
    assert ("POST", "/v1/sitemaps", expected) in fake.calls
    assert ("POST", "/v1/ingestions", {"sitemap_ids": [1]}) in fake.calls
    assert texts(app.success) == ["Crawl 7 started: 2 new pages queued."]


def test_a_preview_with_nothing_new_says_so(use_api: UseApi) -> None:
    use_api({("POST", "/v1/sitemaps"): {**PREVIEW, "new_urls": 0}, ("GET", "/v1/sitemaps"): []})
    app = screen("sources")

    app.text_area[0].input(SITEMAP)
    click(app, "Read the sitemaps")

    assert any("Nothing new to fetch" in caption for caption in texts(app.caption))


def test_a_sitemap_can_be_checked_now(use_api: UseApi) -> None:
    fake = use_api(
        {("GET", "/v1/sitemaps"): [SITEMAP_STATE], ("POST", "/v1/sitemaps/1/check"): SITEMAP_STATE}
    )
    app = screen("sources")

    click(app, "Check now")

    assert ("POST", "/v1/sitemaps/1/check", None) in fake.calls
    assert texts(app.success)[0].startswith("Checking it within a minute")


def test_history_shows_what_changed_in_an_article(use_api: UseApi) -> None:
    diff = "--- before\n+++ after\n@@ -1 +1 @@\n-Click Save.\n+Click Apply."
    versions = [
        {"id": 2, "created_at": WHEN, "title": "Labels", "content_hash": "b" * 64, "diff": diff},
        {"id": 1, "created_at": WHEN, "title": "Labels", "content_hash": "a" * 64, "diff": None},
    ]
    history = {
        "id": 9,
        "url": ARTICLE,
        "title": "Labels",
        "status": "active",
        "missing_count": 0,
        "archived_at": None,
        "versions": versions,
    }
    use_api(
        {
            ("GET", "/v1/articles"): {"total": 0, "items": []},
            ("GET", "/v1/articles/9/history"): history,
        }
    )
    app = screen("history")

    app.number_input[0].set_value(9).run()

    assert "+Click Apply." in texts(app.code)[0]
    assert "The first version: nothing to compare it with" in texts(app.caption)


def test_chat_cites_sends_the_chat_so_far_and_flags_a_wrong_answer(use_api: UseApi) -> None:
    fake = use_api({("POST", "/v1/ask"): answer_json(True), ("POST", "/v1/expert-tasks"): TASK})
    app = screen("chat")

    app.chat_input[0].set_value("How do I rename words?").run()
    app.chat_input[0].set_value("And for buttons?").run()
    [first_flag, _] = [button for button in app.button if button.label == "Wrong answer"]
    first_flag.click().run()

    assert ANSWER in texts(app.markdown)
    assert f"S1: [Text Labels]({ARTICLE})" in texts(app.caption)
    follow_up = fake.calls[1][2]
    assert [turn["role"] for turn in follow_up["history"]] == ["user", "assistant"]
    flag = {"question": "How do I rename words?", "answer": ANSWER}
    assert ("POST", "/v1/expert-tasks", flag) in fake.calls
    assert "Marked as wrong: it's in the expert queue." in texts(app.caption)


def test_chat_says_when_a_question_went_to_an_expert(use_api: UseApi) -> None:
    use_api({("POST", "/v1/ask"): answer_json(False)})
    app = screen("chat")

    app.chat_input[0].set_value("What's the weather?").run()

    assert any(caption.startswith("Sent to an expert as task 4") for caption in texts(app.caption))


def test_the_expert_answers_as_the_signed_in_user(use_api: UseApi) -> None:
    def queue(request: httpx2.Request) -> dict[str, Any]:
        waiting = request.url.params["status"] == "open"
        return {"total": 1, "items": [TASK]} if waiting else {"total": 0, "items": []}

    answered = {**TASK, "status": "answered", "answer": "Check a weather service."}
    fake = use_api(
        {("GET", "/v1/expert-tasks"): queue, ("POST", "/v1/expert-tasks/3/answer"): answered}
    )
    app = screen("experts")

    app.text_area[0].input("Check a weather service.")
    click(app, "Save the answer")

    body = {"answer": "Check a weather service.", "expert": "sam"}
    assert ("POST", "/v1/expert-tasks/3/answer", body) in fake.calls
    assert texts(app.success)[0].startswith("Saved.")
