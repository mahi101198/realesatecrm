"""The voice reasoning seam: the LiveKit Inference tool-calling turn loop.

WHAT IS MOCKED AND WHAT IS NOT
    Exactly one thing is mocked: `voice_llm.get_client()`, i.e. the network. The
    LiveKit SDK's own value types (`ChatContext`, `FunctionCall`,
    `FunctionCallOutput`, `FunctionToolCall`, `CollectedResponse`) are the REAL
    ones, so these tests fail if the SDK changes the shape the loop builds --
    which is the only thing a unit test can usefully pin about a protocol it
    does not own. No credentials, no `.env` and no network are required.

WHY THE ASSERTIONS ARE ABOUT THE CHAT CONTEXT
    The whole risk in this module is transcription between two tool-calling
    dialects: Anthropic echoes `tool_use` blocks and answers them in a user
    turn of `tool_result` blocks; LiveKit correlates a `FunctionCall` item with
    a `FunctionCallOutput` item by `call_id`. Get that wrong and the model sees
    its own tool call unanswered, which on a phone call is a confident lie
    about inventory. So the tests read the context the loop actually built.
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from livekit.agents import llm as lk_llm
from pydantic import SecretStr

from app.core.config import settings
from app.voice import llm as voice_llm
from app.voice.prompts import READ_TOOL_SCHEMAS

# ---------------------------------------------------------------------------
# A fake for the ONE thing we will not talk to: the gateway
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def collect(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    """Stands in for `livekit.agents.inference.LLM`. Records every `chat()`."""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def chat(self, *, chat_ctx: Any, tools: Any = None, **kwargs: Any) -> _FakeStream:
        # Snapshot the items: the loop mutates one ChatContext across rounds,
        # so a live reference would only ever show the final state.
        self.calls.append(
            {"items": list(chat_ctx.items), "tools": tools, "kwargs": kwargs}
        )
        return _FakeStream(self._results.pop(0))


def _text(text: str) -> lk_llm.CollectedResponse:
    return lk_llm.CollectedResponse(text=text)


def _tool_call(name: str, arguments: str, call_id: str = "call_1") -> lk_llm.CollectedResponse:
    return lk_llm.CollectedResponse(
        text="",
        tool_calls=[
            lk_llm.FunctionToolCall(name=name, arguments=arguments, call_id=call_id)
        ],
    )


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    def _install(*results: Any) -> _FakeClient:
        client = _FakeClient(*results)
        monkeypatch.setattr(voice_llm, "get_client", lambda: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# Gating -- fail closed, exactly like the Anthropic seam
# ---------------------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "vendor/model", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", SecretStr("k"), raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SecretStr("s"), raising=False)
    voice_llm.reset_client()
    yield
    voice_llm.reset_client()


def test_configured_requires_the_model_and_both_credentials(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    assert voice_llm.is_voice_llm_configured() is True
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "", raising=False)
    assert voice_llm.is_voice_llm_configured() is False


@pytest.mark.parametrize("field", ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"])
def test_inference_shares_the_room_credentials(
    monkeypatch: pytest.MonkeyPatch, configured: None, field: str
) -> None:
    """There is no separate Inference credential; losing the room pair loses both."""
    monkeypatch.setattr(settings, field, SecretStr(""), raising=False)
    assert voice_llm.is_voice_llm_configured() is False


def test_client_refuses_to_build_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that 401s on the first spoken turn is worse than no client."""
    monkeypatch.setattr(settings, "VOICE_LLM_MODEL", "", raising=False)
    voice_llm.reset_client()
    with pytest.raises(voice_llm.VoiceLLMUnavailableError):
        voice_llm.get_client()


# ---------------------------------------------------------------------------
# Tool schema translation
# ---------------------------------------------------------------------------


def test_the_shared_anthropic_schemas_translate_without_a_second_copy() -> None:
    """One declaration of the tool surface, two wire formats derived from it."""
    tools = voice_llm.build_raw_tools(READ_TOOL_SCHEMAS)

    assert len(tools) == len(READ_TOOL_SCHEMAS)
    by_name = {tool.info.name: tool.info.raw_schema for tool in tools}
    for schema in READ_TOOL_SCHEMAS:
        raw = by_name[schema["name"]]
        assert raw["description"] == schema["description"]
        # `input_schema` (Anthropic) becomes `parameters` (OpenAI), unchanged.
        assert raw["parameters"] == schema["input_schema"]


async def test_the_sdk_is_never_handed_a_working_tool_implementation() -> None:
    """Dispatch belongs to VoiceAgent, behind the allowlist and the read-only
    context. A callable the SDK could run would be a second, unguarded path."""
    tool = voice_llm.build_raw_tools(READ_TOOL_SCHEMAS)[0]
    with pytest.raises(voice_llm.VoiceLLMResponseError):
        await tool()


def test_a_tool_without_a_schema_still_produces_a_valid_parameters_object() -> None:
    tools = voice_llm.build_raw_tools([{"name": "ping"}])
    assert tools[0].info.raw_schema["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# The turn loop
# ---------------------------------------------------------------------------


async def _run(client_results: Any, executor: Any = None, **overrides: Any) -> str:
    return await voice_llm.call_with_tools(
        system="SYSTEM",
        user="USER",
        tools=READ_TOOL_SCHEMAS,
        tool_executor=executor or AsyncMock(return_value={"success": True}),
        **overrides,
    )


async def test_a_turn_with_no_tool_call_returns_the_text(fake_client) -> None:
    client = fake_client(_text("  Yes, we have a 3BHK.  "))
    assert await _run(None) == "Yes, we have a 3BHK."

    items = client.calls[0]["items"]
    assert [i.role for i in items] == ["system", "user"]
    assert client.calls[0]["tools"] is not None


async def test_a_tool_round_trip_correlates_the_call_with_its_output(
    fake_client,
) -> None:
    client = fake_client(
        _tool_call("search_properties", '{"bedrooms": 3, "budget_max": 8000000}'),
        _text("We have a 3BHK at seventy-nine lakhs."),
    )
    executor = AsyncMock(return_value={"success": True, "units": [{"unit_code": "A-1203"}]})

    spoken = await _run(None, executor)

    assert spoken == "We have a 3BHK at seventy-nine lakhs."
    executor.assert_awaited_once_with(
        "search_properties", {"bedrooms": 3, "budget_max": 8000000}
    )

    # The second round must carry the call AND its answer, matched by call_id.
    second_round = client.calls[1]["items"]
    call = next(i for i in second_round if i.type == "function_call")
    output = next(i for i in second_round if i.type == "function_call_output")
    assert call.name == "search_properties"
    assert output.call_id == call.call_id
    assert output.is_error is False
    assert json.loads(output.output)["units"][0]["unit_code"] == "A-1203"
    # Order matters: an output whose call has not been seen yet is dropped.
    assert second_round.index(call) < second_round.index(output)


async def test_parallel_tool_calls_are_all_dispatched_and_all_answered(
    fake_client,
) -> None:
    client = fake_client(
        lk_llm.CollectedResponse(
            tool_calls=[
                lk_llm.FunctionToolCall(
                    name="get_property_details", arguments='{"property_id": "a"}', call_id="c1"
                ),
                lk_llm.FunctionToolCall(
                    name="get_property_availability",
                    arguments='{"property_id": "b"}',
                    call_id="c2",
                ),
            ]
        ),
        _text("Both are available."),
    )
    executor = AsyncMock(return_value={"success": True})

    await _run(None, executor)

    assert executor.await_count == 2
    outputs = [i for i in client.calls[1]["items"] if i.type == "function_call_output"]
    assert {o.call_id for o in outputs} == {"c1", "c2"}


async def test_a_failed_tool_is_reported_to_the_model_as_an_error(fake_client) -> None:
    """`success: False` is information the model must see, not an exception."""
    client = fake_client(
        _tool_call("search_properties", "{}"),
        _text("Let me check with a colleague."),
    )
    executor = AsyncMock(return_value={"success": False, "message": "no matches"})

    await _run(None, executor)

    output = next(i for i in client.calls[1]["items"] if i.type == "function_call_output")
    assert output.is_error is True


async def test_a_raising_tool_never_ends_the_call(fake_client) -> None:
    client = fake_client(
        _tool_call("search_properties", "{}"),
        _text("One moment."),
    )
    executor = AsyncMock(side_effect=RuntimeError("database exploded"))

    assert await _run(None, executor) == "One moment."

    output = next(i for i in client.calls[1]["items"] if i.type == "function_call_output")
    assert output.is_error is True
    assert "failed" in json.loads(output.output)["message"].lower()


@pytest.mark.parametrize("arguments", ["", "not json", "[1, 2, 3]", "null"])
async def test_unparseable_tool_arguments_become_an_empty_call(
    fake_client, arguments: str
) -> None:
    """The model's typo must reach the tool as 'no arguments', not as a crash."""
    fake_client(_tool_call("search_properties", arguments), _text("Sure."))
    executor = AsyncMock(return_value={"success": True})

    await _run(None, executor)

    executor.assert_awaited_once_with("search_properties", {})


async def test_the_loop_is_bounded(fake_client) -> None:
    """A turn that never stops calling tools is a runaway, not a conversation."""
    fake_client(*[_tool_call("search_properties", "{}", f"c{i}") for i in range(3)])
    with pytest.raises(voice_llm.VoiceLLMResponseError, match="did not converge"):
        await _run(None, max_iterations=3)


async def test_an_empty_final_reply_is_an_error_not_silence(fake_client) -> None:
    fake_client(_text("   "))
    with pytest.raises(voice_llm.VoiceLLMResponseError, match="no usable text"):
        await _run(None)


async def test_every_transport_failure_normalises_to_one_exception_family(
    fake_client,
) -> None:
    """Callers get one thing to catch, and one thing to fail toward."""
    fake_client(ConnectionResetError("gateway went away"))
    with pytest.raises(voice_llm.VoiceLLMError):
        await _run(None)


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"outcome": {"type": "string"}},
    "required": ["outcome"],
}


async def _structured(fake_client, text: str) -> dict[str, Any]:
    fake_client(_text(text))
    return await voice_llm.call_structured(system="S", user="U", schema=_SCHEMA)


async def test_structured_asks_the_gateway_to_enforce_the_schema(fake_client) -> None:
    client = fake_client(_text('{"outcome": "connected"}'))
    await voice_llm.call_structured(
        system="S", user="U", schema=_SCHEMA, schema_name="call_outcome"
    )

    response_format = client.calls[0]["kwargs"]["extra_kwargs"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "call_outcome"
    assert response_format["json_schema"]["schema"] is _SCHEMA
    assert response_format["json_schema"]["strict"] is True
    # The schema is ALSO restated in the prompt: gateway enforcement is a
    # per-model property, not a contract this code can rely on.
    assert "outcome" in str(client.calls[0]["items"][0].content)


@pytest.mark.parametrize(
    "text",
    [
        '{"outcome": "connected"}',
        '```json\n{"outcome": "connected"}\n```',
        'Sure! Here you go:\n{"outcome": "connected"}\nHope that helps.',
    ],
)
async def test_json_is_recovered_from_whatever_the_model_wrapped_it_in(
    fake_client, text: str
) -> None:
    """A provider that ignores response_format must degrade to 'we parsed it
    anyway', never to 'the call has no summary'."""
    assert await _structured(fake_client, text) == {"outcome": "connected"}


@pytest.mark.parametrize("text", ["", "I'm sorry, I can't do that.", "[1, 2, 3]"])
async def test_output_with_no_json_object_is_a_typed_failure(
    fake_client, text: str
) -> None:
    with pytest.raises(voice_llm.VoiceLLMResponseError):
        await _structured(fake_client, text)
