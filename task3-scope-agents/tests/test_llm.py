"""The Claude wrapper and the prompt files, against a fake Claude API."""

import json
from decimal import Decimal

import httpx2
import pytest
from pydantic import BaseModel

from app.llm import LLMRefusal, LLMTruncated, cached_text, cost_usd, load_prompt
from tests.fakes import PROMPT_NAMES, ListLedger, make_claude, message_json

KNOWN_MODELS = {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"}


class Echo(BaseModel):
    text: str


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_prompt_file_loads_with_a_known_model(name: str) -> None:
    prompt = load_prompt(name, 1)

    assert prompt.model in KNOWN_MODELS
    assert prompt.fallback_model in KNOWN_MODELS | {None}
    assert "data" in prompt.system  # every prompt says the input is data, not instructions


def test_the_eight_agents_have_their_prompts() -> None:
    assert set(PROMPT_NAMES) == {
        "extract_requirements",
        "merge_requirements",
        "check_fit",
        "clarify_requirements",
        "estimate_tasks",
        "review_scope",
        "write_proposal",
        "analyze_changes",
    }


async def test_call_validates_the_reply_and_logs_its_cost() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=message_json('{"text": "hi"}', model="claude-haiku-5-5"))

    ledger = ListLedger()
    claude = make_claude(handler, ledger)

    result = await claude.call(
        "extract_requirements", 1, [cached_text("hello")], Echo, run_tag="run-1/extractor"
    )

    assert result == Echo(text="hi")
    body = sent[0]
    assert body["model"] == "claude-haiku-5-5"
    # Sampling parameters are an error since API version 2026-10-21
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    assert body["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}],
        }
    ]
    [row] = ledger.rows
    assert (row.prompt, row.prompt_version, row.run_tag, row.success) == (
        "extract_requirements",
        1,
        "run-1/extractor",
        True,
    )
    assert row.cost_usd == Decimal("0.000200")  # 1000 in at $0.10/M + 200 out at $0.50/M


async def test_a_refusal_raises_and_is_logged() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message_json("", stop_reason="refusal"))

    ledger = ListLedger()

    with pytest.raises(LLMRefusal):
        await make_claude(handler, ledger).call("check_fit", 1, "x", Echo)

    assert ledger.rows[0].success is False


async def test_a_cut_off_reply_is_retried_once_with_twice_the_room() -> None:
    sizes: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sizes.append(json.loads(request.content)["max_tokens"])
        return httpx2.Response(200, json=message_json("{", stop_reason="max_tokens"))

    with pytest.raises(LLMTruncated):
        await make_claude(handler).call("check_fit", 1, "x", Echo)

    assert sizes == [4000, 8000]


def test_cost_of_cached_reads() -> None:
    cost = cost_usd("claude-opus-5-5", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000)

    assert cost == Decimal("0.200000")
