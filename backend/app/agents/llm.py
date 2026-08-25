"""The ONE seam between this application and the Anthropic Messages API.

Nothing else in the codebase imports `anthropic`. Two call shapes are exposed
and both are deliberately tiny, because neither can be verified in an
environment without a real `ANTHROPIC_API_KEY`:

    call_structured(...)  -> dict   structured output via output_config.format
                                    (JSON-schema constrained; never free-text
                                    parsing of prose)
    call_with_tools(...)  -> str    bounded manual tool-use loop over a
                                    caller-supplied, READ-ONLY tool surface

Everything around these two functions -- the qualification maths, the routing
table, the webhook wiring -- is deterministic and IS unit tested, by patching
these two names.

FAIL CLOSED
-----------
`is_llm_configured()` is the gate every caller checks first. With no key
configured `get_client()` raises `LLMUnavailableError` rather than
constructing a client that would 401 on first use, so a misconfigured
deployment degrades to "no AI replies" instead of "every webhook logs a stack
trace".

MODEL CHOICE
------------
`settings.ANTHROPIC_MODEL` is the only knob. Thinking is left at the model
default (adaptive on current models) and depth is controlled with
`output_config.effort` -- the `budget_tokens` parameter is removed on current
models and would 400. `max_tokens` caps thinking + response text together,
which is why the default is generous relative to a two-sentence WhatsApp reply.
"""

import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bound on the manual tool-use loop. A WhatsApp reply that needs more than
# this many rounds of property lookups is not a reply, it's a runaway.
DEFAULT_MAX_TOOL_ITERATIONS = 4


class LLMError(RuntimeError):
    """Base class for every failure originating in this module."""


class LLMUnavailableError(LLMError):
    """No usable Anthropic credentials are configured."""


class LLMResponseError(LLMError):
    """The model replied, but not in a shape this application can use."""


_client: AsyncAnthropic | None = None


def is_llm_configured() -> bool:
    """True when an Anthropic API key is present. Callers MUST check this
    before entering any AI code path -- see the module docstring."""
    return bool(settings.ANTHROPIC_API_KEY.get_secret_value().strip())


def get_client() -> AsyncAnthropic:
    """Return the process-wide async Anthropic client.

    Cached because the SDK client owns an HTTP connection pool; building one
    per inbound WhatsApp message would leak sockets under load.
    """
    global _client
    if not is_llm_configured():
        raise LLMUnavailableError(
            "ANTHROPIC_API_KEY is not configured; the AI layer is disabled."
        )
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
    return _client


def reset_client() -> None:
    """Drop the cached client. Only for tests and config reloads."""
    global _client
    _client = None


def _first_text_block(content: Any) -> str | None:
    for block in content or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "") or ""
            if text.strip():
                return text
    return None


async def call_structured(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int | None = None,
    effort: str = "low",
) -> dict[str, Any]:
    """One structured-output call. Returns the parsed JSON object.

    `schema` must be a JSON Schema object with `additionalProperties: false`
    and an explicit `required` list -- structured outputs reject anything
    looser. The response is guaranteed to be a single JSON text block, but we
    still validate rather than trusting it: a `stop_reason` of `max_tokens` or
    `refusal` yields a well-formed response whose text is NOT the schema.
    """
    client = get_client()
    # ignore reason: see call_with_tools below -- plain dicts, not SDK TypedDicts.
    response = await client.messages.create(  # type: ignore[call-overload]
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens or settings.ANTHROPIC_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason in ("refusal", "max_tokens"):
        raise LLMResponseError(
            f"Structured call did not complete (stop_reason={stop_reason!r})."
        )

    text = _first_text_block(getattr(response, "content", None))
    if text is None:
        raise LLMResponseError("Structured call returned no text block.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("Structured call returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError("Structured call returned a non-object payload.")
    return parsed


async def call_with_tools(
    *,
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    tool_executor: Any,
    max_tokens: int | None = None,
    effort: str = "low",
    max_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
) -> str:
    """Run a bounded tool-use loop and return the model's final text.

    `tool_executor` is an async callable `(tool_name, tool_input) -> dict`
    supplied by the caller. This module never decides what a tool does and
    never touches the database; the caller owns the tool surface and is
    responsible for keeping it read-only.

    A manual loop is used rather than the SDK's beta tool runner so the
    outbound surface stays on the stable `client.messages.create` API and the
    iteration bound is explicit and testable.
    """
    client = get_client()
    model = settings.ANTHROPIC_MODEL
    budget = max_tokens or settings.ANTHROPIC_MAX_TOKENS
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    for _ in range(max_iterations):
        # messages/tools are built as plain dicts (not the SDK's TypedDicts) so this
        # module has no SDK-shape imports beyond the client itself; mypy's overload
        # resolution wants the literal TypedDict shapes, but the SDK accepts plain
        # dicts identically at runtime.
        response = await client.messages.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=budget,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": effort},
        )

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMResponseError("Model refused the request.")

        if getattr(response, "stop_reason", None) != "tool_use":
            text = _first_text_block(getattr(response, "content", None))
            if text is None:
                raise LLMResponseError("Model returned no usable text.")
            return text.strip()

        # Append the assistant turn verbatim -- the tool_use blocks must
        # survive intact or the follow-up tool_result cannot be matched.
        messages.append({"role": "assistant", "content": response.content})

        results: list[dict[str, Any]] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                payload = await tool_executor(block.name, dict(block.input or {}))
                is_error = not bool(payload.get("success", True))
            except Exception as exc:  # noqa: BLE001 -- surfaced to the model
                logger.warning(f"AI tool {block.name!r} raised: {exc!s}")
                payload = {"success": False, "message": "Tool execution failed."}
                is_error = True
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, default=str),
                    "is_error": is_error,
                }
            )
        # All results go back in ONE user message; splitting them trains the
        # model out of parallel tool calls.
        messages.append({"role": "user", "content": results})

    raise LLMResponseError(
        f"Tool-use loop did not converge within {max_iterations} iterations."
    )
