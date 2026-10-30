"""The one way the Brain calls Claude.

Every call goes through Claude.call(), which:

1. Loads a versioned prompt file, prompts/<name>/v<N>.md, holding the model, effort, thinking
   mode, max_tokens and system prompt. A change means a new version, never an edit, so each
   logged call points at the exact text it used.
2. Asks for JSON that matches a Pydantic model (structured outputs) and validates the reply.
3. Lets the SDK retry rate limits, overloads, server errors and timeouts with exponential
   backoff, then tries the prompt's fallback model once.
4. Checks stop_reason, so a refusal or a reply cut off by max_tokens never reaches the caller
   as if it were an answer.
5. Writes one row to brain.llm_calls per API call, failures included, with tokens, cost and
   latency. Every cost and speed number in EVALS.md comes from that table.

Message Batches (half price, results within 24 hours) reuse the same prompts, schema check and
ledger through batch_request(), submit_batch() and batch_results().
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol, cast

import anthropic
import yaml
from anthropic import AsyncAnthropic, transform_schema
from anthropic.types import AnthropicBetaParam
from anthropic.types.beta import (
    BetaContentBlockParam,
    BetaJSONOutputFormatParam,
    BetaMessage,
    BetaThinkingConfigParam,
)
from anthropic.types.beta.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.beta.messages.batch_create_params import Request as BatchRequest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import SessionFactory
from app.models import LLMCall

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

# What a prompt runs on: plain text, or content blocks such as an image plus text
type Content = str | list[BetaContentBlockParam]

Effort = Literal["low", "medium", "high", "xhigh", "max"]
Thinking = Literal["adaptive", "disabled", "between_tools"]

# Sonnet and Opus can re-run a refused request on a substitute model that Anthropic picks.
# It's a beta feature, so every call uses the SDK's beta namespace; Haiku has no such fallback.
SERVER_FALLBACK_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5"})
SERVER_FALLBACK_BETA: AnthropicBetaParam = "server-side-fallback-2026-07-01"

# Errors still failing after the SDK's own retries, which a different model may avoid
TRANSIENT_ERRORS = (
    anthropic.RateLimitError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.InternalServerError,
    anthropic.DeadlineExceededError,
    anthropic.APIConnectionError,  # includes timeouts
)


# --- Errors -------------------------------------------------------------------------------


class LLMError(Exception):
    """A call that produced no usable answer. Callers turn this into "I don't know"."""


class LLMRefusal(LLMError):
    """Claude declined to answer (stop_reason "refusal")."""


class LLMTruncated(LLMError):
    """The reply hit max_tokens even after one retry with twice the room."""


class LLMBadOutput(LLMError):
    """The reply wasn't JSON matching the requested schema."""


class LLMUnavailable(LLMError):
    """The model and its fallback both kept failing after retries."""


class PromptNotFound(LookupError):
    """No file for that prompt name and version. A bug in the caller, not an LLM failure."""


# --- Prompt files -------------------------------------------------------------------------


class Prompt(BaseModel):
    """A prompt file: settings from its front matter, and its body as the system prompt."""

    # An unknown setting is an error, so a misspelt key can't be silently ignored
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: int
    model: str
    fallback_model: str | None = None
    effort: Effort
    thinking: Thinking
    max_tokens: int = Field(gt=0)
    changelog: str
    system: str

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}"


@lru_cache
def load_prompt(name: str, version: int, prompts_dir: Path = PROMPTS_DIR) -> Prompt:
    """Read prompts/<name>/v<version>.md: YAML settings between two --- lines, then the prompt."""
    path = prompts_dir / name / f"v{version}.md"
    if not path.is_file():
        available = sorted(file.stem for file in (prompts_dir / name).glob("v*.md"))
        raise PromptNotFound(
            f"No {path.name} for prompt '{name}'. Available: {available or 'none'}"
        )

    parts = path.read_text(encoding="utf-8").split("---\n", 2)
    if len(parts) != 3 or parts[0] != "":
        raise ValueError(f"{path}: put the settings between two '---' lines at the top")
    settings: object = yaml.safe_load(parts[1])
    if not isinstance(settings, dict):
        raise ValueError(f"{path}: the settings must be 'key: value' lines")

    return Prompt.model_validate(
        {
            **cast(dict[str, object], settings),
            "name": name,
            "version": version,
            "system": parts[2].strip(),
        }
    )


# --- Cost ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: Decimal
    output: Decimal
    cache_write: Decimal  # 5-minute cache writes: 1.25 times the input price
    cache_read: Decimal


# From Claude's pricing docs, checked Oct 30, 2026
PRICES = {
    "claude-haiku-5-5": Price(Decimal("0.10"), Decimal("0.50"), Decimal("0.125"), Decimal("0.01")),
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("0.20")),
    "claude-opus-5-5": Price(Decimal("4"), Decimal("20"), Decimal("5"), Decimal("0.20")),
}
# Haiku 5.5 switches to a second rate card when the prompt is over 100K tokens
HAIKU_LONG_PROMPT = Price(Decimal("0.50"), Decimal("2.50"), Decimal("0.625"), Decimal("0.05"))
LONG_PROMPT_TOKENS = 100_000
# Message Batches cost half of every rate
BATCH_DISCOUNT = Decimal("0.5")


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    batch: bool = False,
) -> Decimal:
    """What one call cost. An unknown model is logged at $0 with a warning, so it stands out."""
    # startswith also matches a dated snapshot name such as claude-haiku-5-5-20261001
    price = next((p for name, p in PRICES.items() if model.startswith(name)), None)
    if price is None:
        logger.warning("No price for model %s; logging its cost as $0", model)
        return Decimal(0)
    prompt_tokens = input_tokens + cache_write_tokens + cache_read_tokens
    if model.startswith("claude-haiku-5-5") and prompt_tokens > LONG_PROMPT_TOKENS:
        price = HAIKU_LONG_PROMPT

    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_write_tokens * price.cache_write
        + cache_read_tokens * price.cache_read
    ) / 1_000_000
    if batch:
        total *= BATCH_DISCOUNT
    return total.quantize(Decimal("0.000001"))


# --- The ledger -------------------------------------------------------------------------


class Ledger(Protocol):
    async def record(self, call: LLMCall) -> None: ...


class DatabaseLedger:
    """Saves each row in its own short transaction, so the row is kept even if the caller's
    work rolls back. A failed save is logged, never raised: it must not break an answer."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, call: LLMCall) -> None:
        try:
            async with self._session_factory() as session:
                session.add(call)
                await session.commit()
        except (SQLAlchemyError, OSError):
            logger.exception("Could not save the llm_calls row for %s", call.prompt)


# --- Building requests and reading replies ------------------------------------------------


def thinking_param(mode: Thinking) -> BetaThinkingConfigParam:
    match mode:
        case "adaptive":
            return {"type": "adaptive"}
        case "disabled":
            return {"type": "disabled"}
        case "between_tools":
            return {"type": "between_tools"}


def json_format(output: type[BaseModel]) -> BetaJSONOutputFormatParam:
    """Structured outputs: the reply must be JSON matching this Pydantic model. transform_schema
    rewrites the model's JSON schema into the subset the API accepts."""
    return {"type": "json_schema", "schema": transform_schema(output)}


def request_params(
    prompt: Prompt, model: str, content: Content, output: type[BaseModel], max_tokens: int
) -> MessageCreateParamsNonStreaming:
    """The request body shared by direct calls and batch requests. No temperature: since API
    version 2026-10-21 it's an error on every model."""
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": prompt.system,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": prompt.effort, "format": json_format(output)},
        "thinking": thinking_param(prompt.thinking),
    }


def read_reply[T: BaseModel](message: BetaMessage, output: type[T]) -> T:
    """Turn a reply into an `output` object, or raise if it isn't a complete answer."""
    match message.stop_reason:
        case "end_turn":
            pass
        case "refusal":
            category = message.stop_details.category if message.stop_details else None
            raise LLMRefusal(f"Claude declined to answer (category: {category})")
        case "max_tokens":
            used = message.usage.output_tokens
            raise LLMTruncated(f"The reply hit max_tokens after {used} tokens")
        case other:
            raise LLMError(f"Unexpected stop_reason: {other}")

    # Thinking blocks come first; the JSON answer is the text block
    text = next((block.text for block in message.content if block.type == "text"), None)
    if text is None:
        raise LLMBadOutput("The reply has no text block")
    try:
        return output.model_validate_json(text)
    except ValidationError as error:
        raise LLMBadOutput(f"The reply doesn't match {output.__name__}: {error}") from error


# --- The client ---------------------------------------------------------------------------


class Claude:
    """Runs versioned prompts on Claude and logs every call to the ledger."""

    def __init__(self, client: AsyncAnthropic, ledger: Ledger, prompts_dir: Path = PROMPTS_DIR):
        self._client = client
        self._ledger = ledger
        self._prompts_dir = prompts_dir

    async def call[T: BaseModel](
        self,
        prompt: str,
        version: int,
        content: Content,
        output: type[T],
        *,
        run_tag: str | None = None,
    ) -> T:
        """Run one prompt and return its reply as a validated `output` object.

        Raises an LLMError subclass when there is no usable answer.
        """
        spec = load_prompt(prompt, version, self._prompts_dir)
        models = [spec.model] if spec.fallback_model is None else [spec.model, spec.fallback_model]
        last_error: Exception | None = None
        for model in models:
            try:
                return await self._call_model(spec, model, content, output, run_tag)
            except TRANSIENT_ERRORS as error:
                logger.warning("%s on %s failed after retries: %s", spec.label, model, error)
                last_error = error
        tried = ", ".join(models)
        raise LLMUnavailable(f"{spec.label}: no model answered ({tried})") from last_error

    async def _call_model[T: BaseModel](
        self, spec: Prompt, model: str, content: Content, output: type[T], run_tag: str | None
    ) -> T:
        max_tokens = spec.max_tokens
        while True:
            started = time.perf_counter()
            try:
                message = await self._send(spec, model, content, output, max_tokens)
            except anthropic.APIError as error:
                await self._record(spec, model, _elapsed_ms(started), run_tag, error=error)
                raise
            try:
                result = read_reply(message, output)
            except LLMTruncated as error:
                await self._record(spec, model, _elapsed_ms(started), run_tag, message, error)
                if max_tokens > spec.max_tokens:
                    raise  # already retried once with twice the room
                max_tokens *= 2
            except LLMError as error:
                await self._record(spec, model, _elapsed_ms(started), run_tag, message, error)
                raise
            else:
                await self._record(spec, model, _elapsed_ms(started), run_tag, message)
                return result

    async def _send(
        self, spec: Prompt, model: str, content: Content, output: type[BaseModel], max_tokens: int
    ) -> BetaMessage:
        params = request_params(spec, model, content, output, max_tokens)
        if model in SERVER_FALLBACK_MODELS:
            params["betas"] = [SERVER_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return await self._client.beta.messages.create(**params)

    async def _record(
        self,
        spec: Prompt,
        model: str,
        latency_ms: int,
        run_tag: str | None,
        message: BetaMessage | None = None,
        error: Exception | None = None,
        *,
        batch: bool = False,
    ) -> None:
        """Write one ledger row. The model is the one that answered, which a refusal fallback
        can change from the one requested."""
        usage = message.usage if message else None
        model = message.model if message else model
        input_tokens = usage.input_tokens if usage else 0
        output_tokens = usage.output_tokens if usage else 0
        cache_write = (usage.cache_creation_input_tokens or 0) if usage else 0
        cache_read = (usage.cache_read_input_tokens or 0) if usage else 0
        await self._ledger.record(
            LLMCall(
                prompt=spec.name,
                prompt_version=spec.version,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_write_tokens=cache_write,
                cache_read_tokens=cache_read,
                cost_usd=cost_usd(
                    model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_write_tokens=cache_write,
                    cache_read_tokens=cache_read,
                    batch=batch,
                ),
                latency_ms=latency_ms,
                is_batch=batch,
                success=error is None,
                stop_reason=message.stop_reason if message else None,
                error=f"{type(error).__name__}: {error}" if error else None,
                run_tag=run_tag,
            )
        )

    # --- Message Batches: half price, for work that can wait (screenshot descriptions) ------

    def batch_request(
        self, custom_id: str, prompt: str, version: int, content: Content, output: type[BaseModel]
    ) -> BatchRequest:
        """One request for a batch. Batches can't use the server-side refusal fallback."""
        spec = load_prompt(prompt, version, self._prompts_dir)
        params = request_params(spec, spec.model, content, output, spec.max_tokens)
        return {"custom_id": custom_id, "params": params}

    async def submit_batch(self, requests: list[BatchRequest]) -> str:
        """Send the requests as one Message Batch and return its id, which the caller stores."""
        batch = await self._client.beta.messages.batches.create(requests=requests)
        return batch.id

    async def batch_results[T: BaseModel](
        self,
        batch_id: str,
        prompt: str,
        version: int,
        output: type[T],
        *,
        run_tag: str | None = None,
    ) -> dict[str, T | LLMError] | None:
        """Read a finished batch: each custom_id maps to its answer or to the error it hit.
        Returns None while the batch is still running, so the caller polls again later."""
        batch = await self._client.beta.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return None

        spec = load_prompt(prompt, version, self._prompts_dir)
        results: dict[str, T | LLMError] = {}
        async for item in await self._client.beta.messages.batches.results(batch_id):
            # A batch has no per-request latency, so those rows log 0 ms
            if item.result.type != "succeeded":
                error = LLMError(f"Batch request {item.result.type}")
                results[item.custom_id] = error
                await self._record(spec, spec.model, 0, run_tag, error=error, batch=True)
                continue
            message = item.result.message
            try:
                results[item.custom_id] = read_reply(message, output)
            except LLMError as error:
                results[item.custom_id] = error
                await self._record(spec, spec.model, 0, run_tag, message, error, batch=True)
            else:
                await self._record(spec, spec.model, 0, run_tag, message, batch=True)
        return results


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


@lru_cache
def get_claude() -> Claude:
    """The app's shared client: one connection pool per process, logging to the database."""
    settings = get_settings()
    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value() or None,
        # The SDK retries 408, 409, 429, 5xx and timeouts with exponential backoff
        max_retries=3,
        timeout=90.0,  # seconds per attempt
    )
    return Claude(client, DatabaseLedger(SessionFactory))
