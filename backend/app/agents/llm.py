"""The ONE seam between this application and the Google Gemini API.

Nothing else in the codebase imports `google.genai`. Two call shapes are
exposed and both are deliberately tiny:

    call_structured(...)  -> dict   structured output via response_json_schema
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
`settings.GEMINI_MODEL` is the only knob.

WHY NO TOOL-SCHEMA TRANSLATION LAYER (unlike `app/voice/llm.py`)
------------------------------------------------------------------
`app/voice/llm.py` had to translate this app's Anthropic-shaped tool schemas
(`{name, description, input_schema}`, raw JSON Schema) into LiveKit's
OpenAI-shaped `RawFunctionTool`s. Gemini needs no equivalent step:
`FunctionDeclaration.parameters_json_schema` and
`GenerateContentConfig.response_json_schema` both accept a raw JSON Schema
dict directly -- `READ_TOOL_SCHEMAS`'s `input_schema` and this module's
`schema` argument are handed straight through, untouched.

WHY NO EXPLICIT tool_use_id / is_error PLUMBING (unlike Anthropic)
------------------------------------------------------------------
Anthropic correlates a tool result to its call via an explicit `tool_use_id`
and flags failure with `is_error` on the `tool_result` block. Gemini's
`Part.from_function_response` takes only `name` + a response dict -- there is
no separate error flag, so a failed tool call's `{"success": False, ...}`
payload is handed back as ordinary content and the model reads `success`
itself, exactly like any other tool result. Correlation is positional/by-name
within the turn rather than by an explicit id.
"""

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bound on the manual tool-use loop. A WhatsApp reply that needs more than
# this many rounds of property lookups is not a reply, it's a runaway.
DEFAULT_MAX_TOOL_ITERATIONS = 4

# Finish reasons that mean "the model produced the text/JSON we asked for."
# Anything else (MAX_TOKENS, SAFETY, PROHIBITED_CONTENT, RECITATION, ...) is a
# response whose `.text` is not trustworthy content, exactly like Anthropic's
# `stop_reason in ("refusal", "max_tokens")` check this replaces.
_OK_FINISH_REASONS = frozenset({types.FinishReason.STOP, None})


class LLMError(RuntimeError):
    """Base class for every failure originating in this module."""


class LLMUnavailableError(LLMError):
    """No usable Gemini credentials are configured."""


class LLMResponseError(LLMError):
    """The model replied, but not in a shape this application can use."""


_client: genai.Client | None = None


def is_llm_configured() -> bool:
    """True when a Gemini API key is present. Callers MUST check this
    before entering any AI code path -- see the module docstring."""
    return bool(settings.GEMINI_API_KEY.get_secret_value().strip())


def get_client() -> genai.Client:
    """Return the process-wide async-capable Gemini client.

    Cached because the SDK client owns an HTTP connection pool; building one
    per inbound WhatsApp message would leak sockets under load. Calls go
    through `client.aio.*`, the SDK's async namespace on this same client.
    """
    global _client
    if not is_llm_configured():
        raise LLMUnavailableError(
            "GEMINI_API_KEY is not configured; the AI layer is disabled."
        )
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY.get_secret_value())
    return _client


def reset_client() -> None:
    """Drop the cached client. Only for tests and config reloads."""
    global _client
    _client = None


def _thinking_config(effort: str) -> types.ThinkingConfig | None:
    """Map this app's Anthropic-era `effort` dial onto Gemini's thinking level.

    Only "low" is ever passed by either call site today, so only that case is
    given a real mapping: `thinking_level=MINIMAL` fits a WhatsApp reply /
    intent classification -- both latency-sensitive, neither needing deep
    reasoning. Any other value leaves thinking at the model's adaptive default
    rather than guessing a level.

    NOT `thinking_budget=0`: verified live against the real API that current
    Gemini models (gemini-3.6-flash) reject a zero thinking budget outright
    (400 INVALID_ARGUMENT) -- `thinking_level` is the model generation's
    replacement knob for "as little thinking as possible."
    """
    if effort == "low":
        return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)
    return None


def _finish_reason(response: types.GenerateContentResponse) -> Any:
    candidates = response.candidates or []
    return candidates[0].finish_reason if candidates else None


async def call_structured(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int | None = None,
    effort: str = "low",
) -> dict[str, Any]:
    """One structured-output call. Returns the parsed JSON object.

    `schema` is a JSON Schema object -- passed straight through to
    `response_json_schema`, no translation (see module docstring). The
    response is expected to be exactly one JSON object, but we still validate
    rather than trusting it: a non-STOP `finish_reason` yields a well-formed
    response whose text is NOT the schema (e.g. truncated by MAX_TOKENS).
    """
    client = get_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens or settings.GEMINI_MAX_TOKENS,
        response_mime_type="application/json",
        response_json_schema=schema,
        thinking_config=_thinking_config(effort),
    )
    response = await client.aio.models.generate_content(
        model=settings.GEMINI_MODEL, contents=user, config=config
    )

    finish_reason = _finish_reason(response)
    if finish_reason not in _OK_FINISH_REASONS:
        raise LLMResponseError(
            f"Structured call did not complete (finish_reason={finish_reason!r})."
        )

    text = response.text
    if not text:
        raise LLMResponseError("Structured call returned no text.")
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

    Automatic function calling is explicitly disabled: the SDK can execute
    Python callables itself, but dispatch must stay with the caller's
    allowlisted, permission-scoped `tool_executor` -- handing the SDK a
    callable that could actually reach a tool would create a second,
    unguarded execution path.
    """
    client = get_client()
    model = settings.GEMINI_MODEL
    budget = max_tokens or settings.GEMINI_MAX_TOKENS

    gemini_tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool["name"],
                    description=tool.get("description") or "",
                    parameters_json_schema=tool.get("input_schema")
                    or {"type": "object", "properties": {}, "required": []},
                )
                for tool in tools
            ]
        )
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=budget,
        tools=gemini_tools,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=_thinking_config(effort),
    )

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part.from_text(text=user)])
    ]

    for _ in range(max_iterations):
        response = await client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )

        finish_reason = _finish_reason(response)
        if finish_reason in (
            types.FinishReason.SAFETY,
            types.FinishReason.PROHIBITED_CONTENT,
            types.FinishReason.BLOCKLIST,
            types.FinishReason.RECITATION,
        ):
            raise LLMResponseError(f"Model refused the request (finish_reason={finish_reason!r}).")

        function_calls = response.function_calls or []
        if not function_calls:
            text = (response.text or "").strip()
            if not text:
                raise LLMResponseError("Model returned no usable text.")
            return text

        # Replay the model's own turn (its function-call parts) verbatim --
        # `candidates[0].content` already carries them, so there is nothing to
        # reconstruct, unlike Anthropic's tool_use blocks.
        candidates = response.candidates or []
        contents.append(candidates[0].content)

        response_parts: list[types.Part] = []
        for call in function_calls:
            try:
                payload = await tool_executor(call.name, dict(call.args or {}))
            except Exception as exc:  # noqa: BLE001 -- surfaced to the model
                logger.warning(f"AI tool {call.name!r} raised: {exc!s}")
                payload = {"success": False, "message": "Tool execution failed."}
            response_parts.append(
                types.Part.from_function_response(name=call.name, response=payload)
            )
        # All results go back in ONE user turn; splitting them trains the
        # model out of parallel tool calls.
        contents.append(types.Content(role="user", parts=response_parts))

    raise LLMResponseError(
        f"Tool-use loop did not converge within {max_iterations} iterations."
    )
