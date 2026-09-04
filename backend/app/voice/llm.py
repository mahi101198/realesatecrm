"""The voice package's reasoning seam: LiveKit Inference, NOT Anthropic.

WHY A SECOND LLM BACKEND EXISTS
    `app/agents/llm.py` is and stays the ONE Anthropic seam -- the WhatsApp
    agent and the lead orchestrator reason through it and are untouched by this
    module. Voice is different in kind, not in taste: a spoken turn is
    STT -> reason -> TTS inside a latency budget a human notices, and LiveKit's
    Inference Gateway serves all three from the edge the media is already on,
    authenticated by the LIVEKIT_API_KEY/SECRET pair the room already uses. A
    cross-cloud hop to a second vendor purely for the middle step is the one
    thing that would be felt on every single turn.

    So: two parallel backends, one per channel. Nothing here is imported by the
    WhatsApp path, and nothing here changes Anthropic's behaviour.

THE SHAPE IS DELIBERATELY THE SAME AS `app/agents/llm.py`
    `call_with_tools(...) -> str` and `call_structured(...) -> dict`, same
    keyword arguments, same `tool_executor` contract, same
    `LLMError`/`Unavailable`/`Response` exception triple. `VoiceAgent` therefore
    reads identically whichever backend it is pointed at, and the swap is one
    import. The bodies differ because the wire protocols differ:

      Anthropic                          LiveKit Inference (OpenAI-shaped)
      ---------                          ---------------------------------
      tools=[{name, input_schema}]       tools=[RawFunctionTool]  (name/parameters)
      response.content -> tool_use       LLMStream -> FunctionToolCall
      user turn of tool_result blocks    ChatContext.insert(FunctionCall,
                                           FunctionCallOutput)
      output_config.format=json_schema   response_format=json_schema, and only
                                           via extra_kwargs -- see below

STRUCTURED OUTPUT: THE ONE REAL DIFFERENCE, STATED PLAINLY
    Anthropic's `output_config.format` GUARANTEES schema-conformant output.
    Here the equivalent is OpenAI's `response_format={"type": "json_schema",
    ...}`, which this SDK will only accept as a *typed* Python class through its
    `response_format=` parameter; a plain JSON-Schema dict raises
    "Unsupported response_format type". Building a pydantic model at runtime
    from `OUTCOME_SCHEMA` just to have the SDK convert it straight back to that
    same schema is ceremony, so the raw dict is passed through `extra_kwargs`,
    which the SDK forwards to the gateway verbatim.

    Verified live against `google/gemma-4-31b-it` on the real gateway: strict
    json_schema is accepted and honoured. But strictness is a per-model,
    per-provider property of the gateway, not a contract this code can enforce,
    so `call_structured` ALSO asks for JSON in the prompt and parses
    defensively (fenced ```json blocks are unwrapped, the first JSON object in
    the text is recovered). Callers must keep validating the parsed dict --
    `VoiceAgent.summarise` still clamps `outcome` to the DB enum -- exactly as
    they did with Anthropic. That is the honest difference: same schema, same
    caller-side validation, weaker provider-side guarantee.

FAIL CLOSED
    `is_voice_llm_configured()` gates every entry point. With no LiveKit
    credentials or no `VOICE_LLM_MODEL`, `get_client()` raises
    `VoiceLLMUnavailableError` instead of building a client that would 401 on
    the first spoken turn.
"""

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover -- typing only, never imported at runtime
    from livekit.agents import inference as lk_inference

logger = logging.getLogger(__name__)

# Bound on the tool-use loop, same reasoning as the Anthropic seam: a spoken
# turn that needs more than this many property lookups is not a turn, it is a
# runaway, and the customer is listening to silence while it runs.
DEFAULT_MAX_TOOL_ITERATIONS = 4

# Fenced code blocks are the most common way a chat-completion model wraps JSON
# when the gateway did not enforce a schema. Cheap to unwrap, so we do.
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class VoiceLLMError(RuntimeError):
    """Base class for every failure originating in this module."""


class VoiceLLMUnavailableError(VoiceLLMError):
    """No usable LiveKit Inference configuration (credentials or model id)."""


class VoiceLLMResponseError(VoiceLLMError):
    """The model replied, but not in a shape this application can use."""


_client: "lk_inference.LLM | None" = None


def is_voice_llm_configured() -> bool:
    """True when the voice reasoning plane can actually be reached.

    Callers MUST check this before entering any voice AI code path. Note it
    requires the LiveKit credential pair as well as the model id: Inference is
    authenticated with the same key/secret as the rooms, so there is no
    separate credential to miss.
    """
    return bool(
        settings.VOICE_LLM_MODEL.strip()
        and settings.LIVEKIT_API_KEY.get_secret_value().strip()
        and settings.LIVEKIT_API_SECRET.get_secret_value().strip()
    )


def get_client() -> "lk_inference.LLM":
    """Return the process-wide LiveKit Inference LLM client.

    Cached for the same reason the Anthropic client is: it owns an HTTP
    connection pool, and building one per spoken turn would leak sockets at
    exactly the moment latency matters. The gateway access token is minted
    fresh inside every `chat()` call by the SDK, so a long-lived client does
    not go stale.
    """
    global _client
    if not is_voice_llm_configured():
        raise VoiceLLMUnavailableError(
            "VOICE_LLM_MODEL / LiveKit credentials are not configured; "
            "the voice reasoning plane is disabled."
        )
    if _client is None:
        from livekit.agents import inference as lk_inference

        _client = lk_inference.LLM(
            model=settings.VOICE_LLM_MODEL,
            api_key=settings.LIVEKIT_API_KEY.get_secret_value(),
            api_secret=settings.LIVEKIT_API_SECRET.get_secret_value(),
        )
    return _client


def reset_client() -> None:
    """Drop the cached client. Only for tests and config reloads."""
    global _client
    _client = None


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------


def build_raw_tools(schemas: list[dict[str, Any]]) -> list[Any]:
    """Translate this app's Anthropic-shaped tool schemas into LiveKit tools.

    `READ_TOOL_SCHEMAS` is declared once, in Anthropic's `{name, description,
    input_schema}` shape, and shared by both agents. Rather than fork it into a
    second OpenAI-shaped copy that could drift, it is translated here into
    `RawFunctionTool`s -- the SDK's escape hatch that forwards a raw JSON
    Schema to the provider untouched, instead of deriving one by introspecting
    a Python signature.

    The wrapped callable is a stub that is never invoked: dispatch stays in
    `VoiceAgent._execute_tool`, behind the allowlist and the permission-less
    read context. Handing the SDK a callable that could actually reach the tool
    registry would create a second, unguarded execution path, which is the one
    thing the allowlist exists to prevent.
    """
    from livekit.agents import llm as lk_llm

    async def _never_called(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        # Defensive, not decorative: if a future SDK path ever executed tools
        # itself, this must not silently succeed.
        raise VoiceLLMResponseError(
            "Voice tools are dispatched by VoiceAgent, never by the SDK."
        )

    tools: list[Any] = []
    for schema in schemas:
        tools.append(
            lk_llm.function_tool(
                _never_called,
                raw_schema={
                    "name": schema["name"],
                    "description": schema.get("description") or "",
                    "parameters": schema.get("input_schema")
                    or {"type": "object", "properties": {}, "required": []},
                },
            )
        )
    return tools


def _parse_tool_arguments(name: str, raw: str) -> dict[str, Any]:
    """Decode a tool call's JSON argument string. Never raises.

    Malformed arguments become an empty dict, which the tool then rejects (or
    answers with its defaults) and the model sees the result -- strictly better
    than an exception that would end a live call over the model's typo.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Voice LLM sent unparseable arguments for tool {name!r}; ignoring them.")
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# The two call shapes
# ---------------------------------------------------------------------------


async def _run_one_tool(tool_executor: Any, call: Any) -> tuple[dict[str, Any], bool]:
    """Run one tool call, normalising a raised exception into an error payload.

    Split out of the loop so `call_with_tools` can `asyncio.gather` several of
    these at once: each one is fully self-contained (parses its own
    arguments, catches its own failure), so nothing here depends on execution
    order relative to any other call in the same round.
    """
    arguments = _parse_tool_arguments(call.name, call.arguments)
    try:
        payload = await tool_executor(call.name, arguments)
        is_error = not bool(payload.get("success", True))
    except Exception as exc:  # noqa: BLE001 -- surfaced to the model, not raised
        logger.warning(f"Voice AI tool {call.name!r} raised: {exc!s}")
        payload = {"success": False, "message": "Tool execution failed."}
        is_error = True
    return payload, is_error


async def call_with_tools(
    *,
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    tool_executor: Any,
    max_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
) -> str:
    """Run a bounded tool-use loop and return the model's final spoken text.

    `tool_executor` is an async callable `(tool_name, tool_input) -> dict`
    supplied by the caller, identical to `app/agents/llm.py`. This module never
    decides what a tool does and never touches the database.

    The loop mirrors the Anthropic one round for round. The only structural
    difference is how a round is appended to the conversation: Anthropic wants
    the assistant's `tool_use` blocks echoed back verbatim followed by a user
    turn of `tool_result` blocks, whereas LiveKit's `ChatContext` takes a
    `FunctionCall` item and a matching `FunctionCallOutput` item correlated by
    `call_id`. Both encode the same thing -- "you asked for this, here is what
    it returned".
    """
    client = get_client()
    lk_tools = build_raw_tools(tools)

    from livekit.agents import llm as lk_llm

    chat_ctx = lk_llm.ChatContext()
    chat_ctx.add_message(role="system", content=system)
    chat_ctx.add_message(role="user", content=user)

    for _ in range(max_iterations):
        response = await _collect(client.chat(chat_ctx=chat_ctx, tools=lk_tools))

        if not response.tool_calls:
            text = (response.text or "").strip()
            if not text:
                raise VoiceLLMResponseError("Model returned no usable text.")
            return text

        # Echo the calls back before their outputs: a FunctionCallOutput whose
        # call_id has no preceding FunctionCall is dropped when the context is
        # serialised, and the model would see its own tool call unanswered.
        for call in response.tool_calls:
            chat_ctx.insert(
                lk_llm.FunctionCall(
                    call_id=call.call_id, name=call.name, arguments=call.arguments
                )
            )

        # Concurrent, not sequential: a turn where the model asks for two
        # independent lookups (e.g. a property AND its project) used to pay
        # for both round trips back-to-back, purely because this loop awaited
        # them one at a time. `tool_executor` (VoiceAgent._execute_tool) opens
        # its own short-lived DB session per call precisely so this gather is
        # safe -- see that method's docstring for why a shared AsyncSession
        # could not support this.
        results = await asyncio.gather(
            *(_run_one_tool(tool_executor, call) for call in response.tool_calls)
        )
        for call, (payload, is_error) in zip(response.tool_calls, results, strict=True):
            chat_ctx.insert(
                lk_llm.FunctionCallOutput(
                    call_id=call.call_id,
                    name=call.name,
                    output=json.dumps(payload, default=str),
                    is_error=is_error,
                )
            )

    raise VoiceLLMResponseError(
        f"Tool-use loop did not converge within {max_iterations} iterations."
    )


async def call_structured(
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str = "result",
) -> dict[str, Any]:
    """One structured-output call. Returns the parsed JSON object.

    Asks the gateway for strict `json_schema` output AND restates the contract
    in the prompt, then parses defensively. See the module docstring for why
    both belts are worn: the schema is enforced by the gateway for the models
    that support it, and by this parser plus the caller's own validation for
    the ones that do not.
    """
    client = get_client()

    from livekit.agents import llm as lk_llm

    chat_ctx = lk_llm.ChatContext()
    chat_ctx.add_message(
        role="system",
        content=(
            f"{system}\n\nReply with ONE JSON object and nothing else -- no prose, "
            "no code fence, no explanation. It must match this JSON Schema exactly:\n"
            f"{json.dumps(schema)}"
        ),
    )
    chat_ctx.add_message(role="user", content=user)

    response = await _collect(
        client.chat(
            chat_ctx=chat_ctx,
            # Passed through `extra_kwargs` rather than the SDK's typed
            # `response_format=` parameter, which only accepts a pydantic model
            # or TypedDict class. See the module docstring.
            extra_kwargs={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                }
            },
        )
    )

    parsed = _extract_json_object(response.text or "")
    if parsed is None:
        raise VoiceLLMResponseError("Structured call did not return a JSON object.")
    return parsed


async def _collect(stream: Any) -> Any:
    """Drain an `LLMStream` into a `CollectedResponse`, normalising failures.

    Every transport, auth, rate-limit and gateway error the SDK raises becomes
    a `VoiceLLMError`, so callers have exactly one exception family to catch --
    and, in the voice agent's case, exactly one thing to fail toward.
    """
    try:
        return await stream.collect()
    except Exception as exc:  # noqa: BLE001 -- normalised, see docstring
        raise VoiceLLMResponseError(f"LiveKit Inference call failed: {exc!s}") from exc


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Recover a JSON object from model output. Returns None if there isn't one.

    Three attempts, cheapest first: the whole string, the contents of a ```json
    fence, then the outermost brace-delimited span. A model that was given a
    strict schema needs only the first; the rest exist so a provider that
    ignores `response_format` degrades to "we parsed it anyway" instead of "the
    call has no summary".
    """
    candidates = [text.strip()]
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
