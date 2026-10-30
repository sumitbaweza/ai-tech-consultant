"""The Claude wrapper, run against a fake Claude API: no network, no key, no cost."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from app.llm import (
    LLMError,
    LLMRefusal,
    LLMTruncated,
    LLMUnavailable,
    PromptNotFound,
    cost_usd,
    load_prompt,
)
from tests.fakes import ListLedger, batch_json, make_claude, message_json


class Echo(BaseModel):
    """The output schema the test prompts ask for."""

    text: str


HAIKU_PROMPT = """---
model: claude-haiku-5-5
effort: low
thinking: disabled
max_tokens: 100
changelog: Test prompt.
---
Repeat the user's text.
"""

SONNET_PROMPT = """---
model: claude-sonnet-5-5
fallback_model: claude-haiku-5-5
effort: medium
thinking: adaptive
max_tokens: 100
changelog: Test prompt with a fallback model.
---
Repeat the user's text.
"""

OVERLOADED = httpx2.Response(
    529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
)


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    for name, text in [("echo", HAIKU_PROMPT), ("echo_sonnet", SONNET_PROMPT)]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "v1.md").write_text(text, encoding="utf-8")
    return tmp_path


class FakeClaudeAPI:
    """Answers each request with the next canned reply, and records what was sent."""

    def __init__(self, *replies: httpx2.Response) -> None:
        self.replies = list(replies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.replies.pop(0)

    def body(self, index: int) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def message(text: str, **kwargs: Any) -> httpx2.Response:
    return httpx2.Response(200, json=message_json(text, **kwargs))


# --- Prompt files -------------------------------------------------------------------------


def test_prompt_file_gives_settings_and_system_prompt(prompts_dir: Path) -> None:
    prompt = load_prompt("echo", 1, prompts_dir)

    assert (prompt.model, prompt.effort, prompt.thinking) == ("claude-haiku-5-5", "low", "disabled")
    assert prompt.system == "Repeat the user's text."
    assert prompt.label == "echo@v1"


def test_unknown_version_lists_the_versions_that_exist(prompts_dir: Path) -> None:
    with pytest.raises(PromptNotFound, match=r"\['v1'\]"):
        load_prompt("echo", 2, prompts_dir)


def test_misspelt_setting_is_an_error(prompts_dir: Path) -> None:
    misspelt = HAIKU_PROMPT.replace("effort:", "efort:")
    (prompts_dir / "echo" / "v2.md").write_text(misspelt, encoding="utf-8")

    with pytest.raises(ValidationError):
        load_prompt("echo", 2, prompts_dir)


# --- Calls --------------------------------------------------------------------------------


async def test_call_returns_the_validated_reply_and_logs_its_cost(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(message('{"text": "hello"}', input_tokens=1000, output_tokens=200))
    ledger = ListLedger()

    result = await make_claude(api, prompts_dir, ledger).call(
        "echo", 1, "hello", Echo, run_tag="test"
    )

    assert result == Echo(text="hello")
    sent = api.body(0)
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["system"] == "Repeat the user's text."
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in sent
    assert "fallbacks" not in sent  # Haiku has no server-side refusal fallback
    [row] = ledger.rows
    assert (row.prompt, row.prompt_version, row.model) == ("echo", 1, "claude-haiku-5-5")
    assert (row.input_tokens, row.output_tokens, row.success) == (1000, 200, True)
    assert row.cost_usd == Decimal("0.000200")  # 1,000 x $0.10/M + 200 x $0.50/M
    assert row.run_tag == "test"


async def test_sonnet_asks_for_the_server_side_refusal_fallback(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(message('{"text": "hi"}', model="claude-sonnet-5-5"))

    await make_claude(api, prompts_dir).call("echo_sonnet", 1, "hi", Echo)

    assert api.body(0)["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.requests[0].headers["anthropic-beta"]


async def test_refusal_raises_and_is_logged_as_a_failure(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(message("", stop_reason="refusal"))
    ledger = ListLedger()

    with pytest.raises(LLMRefusal):
        await make_claude(api, prompts_dir, ledger).call("echo", 1, "hello", Echo)

    [row] = ledger.rows
    assert (row.success, row.stop_reason) == (False, "refusal")


async def test_cut_off_reply_gets_one_retry_with_twice_the_room(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(message('{"te', stop_reason="max_tokens"), message('{"text": "hello"}'))
    ledger = ListLedger()

    result = await make_claude(api, prompts_dir, ledger).call("echo", 1, "hello", Echo)

    assert result.text == "hello"
    assert [api.body(0)["max_tokens"], api.body(1)["max_tokens"]] == [100, 200]
    assert [row.success for row in ledger.rows] == [False, True]


async def test_reply_cut_off_twice_raises(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(
        message('{"te', stop_reason="max_tokens"), message('{"tex', stop_reason="max_tokens")
    )

    with pytest.raises(LLMTruncated):
        await make_claude(api, prompts_dir).call("echo", 1, "hello", Echo)


async def test_overloaded_model_falls_back_to_the_prompts_fallback_model(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(OVERLOADED, message('{"text": "hi"}', model="claude-haiku-5-5"))
    ledger = ListLedger()

    result = await make_claude(api, prompts_dir, ledger).call("echo_sonnet", 1, "hi", Echo)

    assert result.text == "hi"
    assert [api.body(0)["model"], api.body(1)["model"]] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert [(row.model, row.success) for row in ledger.rows] == [
        ("claude-sonnet-5-5", False),
        ("claude-haiku-5-5", True),
    ]


async def test_no_fallback_model_means_unavailable(prompts_dir: Path) -> None:
    api = FakeClaudeAPI(OVERLOADED)

    with pytest.raises(LLMUnavailable):
        await make_claude(api, prompts_dir).call("echo", 1, "hi", Echo)


# --- Cost ---------------------------------------------------------------------------------


def test_haiku_switches_rate_card_above_100k_prompt_tokens() -> None:
    # 100K in at $0.10/M + 1K out at $0.50/M; then 200K in at $0.50/M + 1K out at $2.50/M
    assert cost_usd("claude-haiku-5-5", input_tokens=100_000, output_tokens=1_000) == Decimal(
        "0.010500"
    )
    assert cost_usd("claude-haiku-5-5", input_tokens=200_000, output_tokens=1_000) == Decimal(
        "0.102500"
    )


def test_cache_tokens_and_batches_are_priced() -> None:
    # Sonnet 5.5: 1M cache reads at $0.20 + 1M cache writes at $2.50
    cached = cost_usd(
        "claude-sonnet-5-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cached == Decimal("2.700000")
    # A batch pays half: 1M input tokens at $2/M becomes $1
    assert cost_usd("claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=0, batch=True) == (
        Decimal("1.000000")
    )


def test_unknown_model_is_logged_at_zero_cost() -> None:
    assert cost_usd("some-other-model", input_tokens=10, output_tokens=10) == Decimal(0)


# --- Message Batches ----------------------------------------------------------------------


class FakeBatchAPI:
    """Answers the batch endpoints: creating or reading the batch, and its JSONL results file."""

    def __init__(self, status: str, results: list[dict[str, Any]] | None = None) -> None:
        self.status = status
        self.results = results or []
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path.endswith("/results"):
            lines = "\n".join(json.dumps(line) for line in self.results)
            return httpx2.Response(200, content=lines.encode())
        return httpx2.Response(200, json=batch_json(self.status))


async def test_batch_requests_are_submitted_without_the_refusal_fallback(
    prompts_dir: Path,
) -> None:
    api = FakeBatchAPI("in_progress")
    claude = make_claude(api, prompts_dir)

    request = claude.batch_request("image-1", "echo_sonnet", 1, "hello", Echo)
    batch_id = await claude.submit_batch([request])

    assert batch_id == "msgbatch_test"
    [sent] = json.loads(api.requests[0].content)["requests"]
    assert sent["custom_id"] == "image-1"
    assert sent["params"]["model"] == "claude-sonnet-5-5"
    assert "fallbacks" not in sent["params"]


async def test_batch_still_running_returns_none(prompts_dir: Path) -> None:
    claude = make_claude(FakeBatchAPI("in_progress"), prompts_dir)

    assert await claude.batch_results("msgbatch_test", "echo", 1, Echo) is None


async def test_batch_results_are_parsed_and_logged_at_half_price(prompts_dir: Path) -> None:
    errored = {"type": "error", "error": {"type": "invalid_request_error", "message": "Bad image"}}
    api = FakeBatchAPI(
        "ended",
        [
            {
                "custom_id": "image-1",
                "result": {"type": "succeeded", "message": message_json('{"text": "a chart"}')},
            },
            {"custom_id": "image-2", "result": {"type": "errored", "error": errored}},
        ],
    )
    ledger = ListLedger()

    results = await make_claude(api, prompts_dir, ledger).batch_results(
        "msgbatch_test", "echo", 1, Echo
    )

    assert results is not None
    assert results["image-1"] == Echo(text="a chart")
    assert isinstance(results["image-2"], LLMError)
    assert [(row.is_batch, row.success) for row in ledger.rows] == [(True, True), (True, False)]
    assert ledger.rows[0].cost_usd == Decimal("0.000100")  # half of the $0.0002 direct price
