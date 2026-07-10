"""
Unit tests for the Perplexity / Responses-API schema translators.

These exercise the two pure-function converters that bridge our
Anthropic-style internal calls and OpenAI's Responses API schema. No
network calls ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the OpenAI client is mocked via a stub object.

Why bother with translator unit tests:
  * The RCA ReAct loop emits multi-turn message lists with ``tool_use`` and
    ``tool_result`` blocks. Getting the ``call_id`` pairing wrong silently
    breaks tool dispatch on the Perplexity side ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â these tests pin that down.
  * The same translator handles screenshot blocks emitted by the
    ``get_step_details`` tool; ensure they don't crash the converter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from debugger.perplexity_adapter import (
    PerplexityResponsesAdapter,
    _anthropic_msgs_to_responses_input,
    _anthropic_tools_to_responses,
    _responses_output_to_anthropic,
)
from debugger.together_adapter import AnthropicResponse, TextBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Anthropic -> Responses-API input array
# ---------------------------------------------------------------------------


class TestMessagesToResponsesInput:
    """Every shape the RCA / discussion loops can emit must round-trip."""

    def test_plain_user_string(self) -> None:
        out = _anthropic_msgs_to_responses_input(
            [{"role": "user", "content": "hello world"}],
        )
        assert out == [{
            "type":    "message",
            "role":    "user",
            "content": [{"type": "input_text", "text": "hello world"}],
        }]

    def test_plain_assistant_string(self) -> None:
        out = _anthropic_msgs_to_responses_input(
            [{"role": "assistant", "content": "hi there"}],
        )
        # Assistant content uses output_text, not input_text.
        assert out == [{
            "type":    "message",
            "role":    "assistant",
            "content": [{"type": "output_text", "text": "hi there"}],
        }]

    def test_assistant_text_plus_tool_use(self) -> None:
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "text",     "text": "I'll look that up."},
                {"type": "tool_use", "id": "toolu_42",
                 "name": "get_step_details", "input": {"step_num": 3}},
            ],
        }]
        out = _anthropic_msgs_to_responses_input(msgs)
        # First item is the message with output_text; second is the function_call.
        assert len(out) == 2
        assert out[0]["type"] == "message"
        assert out[0]["role"] == "assistant"
        assert out[0]["content"][0]["text"] == "I'll look that up."
        assert out[1] == {
            "type":      "function_call",
            "call_id":   "toolu_42",
            "name":      "get_step_details",
            "arguments": json.dumps({"step_num": 3}),
        }

    def test_assistant_text_only_blocks(self) -> None:
        """Assistant message with only text blocks emits one merged message."""
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first sentence."},
                {"type": "text", "text": "second sentence."},
            ],
        }]
        out = _anthropic_msgs_to_responses_input(msgs)
        assert len(out) == 1
        assert out[0]["type"] == "message"
        assert out[0]["role"] == "assistant"
        assert out[0]["content"] == [{"type": "output_text", "text": "first sentence.\nsecond sentence."}]

    def test_assistant_drops_thinking_blocks(self) -> None:
        """Thinking blocks are private to the model and must not be echoed."""
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "secret deliberation"},
                {"type": "text",     "text":     "visible reply"},
            ],
        }]
        out = _anthropic_msgs_to_responses_input(msgs)
        # Only the visible reply should appear.
        assert "secret deliberation" not in json.dumps(out)
        assert out[0]["content"][0]["text"] == "visible reply"

    def test_user_tool_results(self) -> None:
        """User-role tool_result blocks ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ function_call_output items."""
        msgs = [{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_42",
                 "content": "raw text output"},
                {"type": "tool_result", "tool_use_id": "toolu_43",
                 "content": [{"type": "text", "text": "block text"}]},
            ],
        }]
        out = _anthropic_msgs_to_responses_input(msgs)
        assert out == [
            {"type": "function_call_output", "call_id": "toolu_42",
             "output": "raw text output"},
            {"type": "function_call_output", "call_id": "toolu_43",
             "output": "block text"},
        ]

    def test_user_tool_result_with_image_is_placeholdered(self) -> None:
        """Image blocks inside a tool_result are split into a follow-up image message."""
        msgs = [{
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_99",
                "content": [
                    {"type": "text",  "text": "Step details:"},
                    {"type": "image", "source": {"data": "...", "media_type": "image/png"}},
                ],
            }],
        }]
        out = _anthropic_msgs_to_responses_input(msgs)
        assert len(out) == 2
        assert out[0]["type"] == "function_call_output"
        assert "Step details:" in out[0]["output"]
        assert "Screenshot attached" in out[0]["output"]
        assert out[1]["type"] == "message"
        assert out[1]["content"][0]["type"] == "input_image"

    def test_full_rca_ish_trace(self) -> None:
        """Sanity end-to-end: user ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ assistant(tool_use) ÃƒÂ¢Ã¢â‚¬Â Ã¢â‚¬â„¢ user(tool_result)."""
        msgs = [
            {"role": "user", "content": "Inspect step 3."},
            {"role": "assistant", "content": [
                {"type": "text", "text": "calling tool"},
                {"type": "tool_use", "id": "toolu_1",
                 "name": "get_step_details", "input": {"step_num": 3}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "step 3 details"},
            ]},
        ]
        out = _anthropic_msgs_to_responses_input(msgs)
        # 4 items: initial user msg, assistant text, assistant function_call,
        # user function_call_output.
        assert [item["type"] for item in out] == [
            "message", "message", "function_call", "function_call_output",
        ]
        # call_id wires the function_call to the function_call_output.
        assert out[2]["call_id"] == out[3]["call_id"] == "toolu_1"


# ---------------------------------------------------------------------------
# Anthropic tool descriptors -> Responses tool descriptors
# ---------------------------------------------------------------------------


class TestToolsConversion:
    def test_basic_tool_shape(self) -> None:
        tools = [{
            "name": "get_weather",
            "description": "Look up the weather.",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }]
        out = _anthropic_tools_to_responses(tools)
        assert out == [{
            "type":        "function",
            "name":        "get_weather",
            "description": "Look up the weather.",
            "parameters":  tools[0]["input_schema"],
        }]

    def test_missing_description_defaults_to_empty(self) -> None:
        out = _anthropic_tools_to_responses([{"name": "x"}])
        assert out[0]["description"] == ""
        assert out[0]["parameters"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Responses-API output -> AnthropicResponse
# ---------------------------------------------------------------------------


@dataclass
class _StubSub:
    type: str
    text: str | None = None


@dataclass
class _StubItem:
    type: str
    content: list = field(default_factory=list)
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None


@dataclass
class _StubResponse:
    output: list = field(default_factory=list)
    status: str | None = None
    incomplete_details: object | None = None


class TestResponsesToAnthropic:
    def test_pure_text_output(self) -> None:
        resp = _StubResponse(output=[
            _StubItem(type="message",
                      content=[_StubSub(type="output_text", text="OK")]),
        ])
        result = _responses_output_to_anthropic(resp)
        assert isinstance(result, AnthropicResponse)
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert isinstance(result.content[0], TextBlock)
        assert result.content[0].text == "OK"

    def test_function_call_only(self) -> None:
        resp = _StubResponse(output=[
            _StubItem(type="function_call",
                      call_id="toolu_xyz",
                      name="get_step_details",
                      arguments='{"step_num": 4}'),
        ])
        result = _responses_output_to_anthropic(resp)
        assert result.stop_reason == "tool_use"
        assert len(result.content) == 1
        block = result.content[0]
        assert isinstance(block, ToolUseBlock)
        assert block.name  == "get_step_details"
        assert block.input == {"step_num": 4}
        assert block.id    == "toolu_xyz"

    def test_text_plus_function_call_mixed(self) -> None:
        """Single response with both an output_text and a function_call."""
        resp = _StubResponse(output=[
            _StubItem(type="message",
                      content=[_StubSub(type="output_text", text="thinking...")]),
            _StubItem(type="function_call",
                      call_id="toolu_1", name="x", arguments="{}"),
        ])
        result = _responses_output_to_anthropic(resp)
        assert result.stop_reason == "tool_use"
        types = [type(b).__name__ for b in result.content]
        assert types == ["TextBlock", "ToolUseBlock"]

    def test_malformed_arguments_default_to_empty_dict(self) -> None:
        resp = _StubResponse(output=[
            _StubItem(type="function_call",
                      call_id="x", name="y", arguments="this is not json"),
        ])
        result = _responses_output_to_anthropic(resp)
        assert result.content[0].input == {}

    def test_function_call_with_no_call_id_gets_synthetic_one(self) -> None:
        resp = _StubResponse(output=[
            _StubItem(type="function_call",
                      call_id=None, name="y", arguments="{}"),
        ])
        result = _responses_output_to_anthropic(resp)
        assert result.content[0].id.startswith("toolu_")

    def test_max_tokens_status_reflected(self) -> None:
        @dataclass
        class _Details: reason: str = "max_output_tokens"
        resp = _StubResponse(
            output=[_StubItem(type="message",
                              content=[_StubSub(type="output_text",
                                                text="partial")])],
            status="incomplete",
            incomplete_details=_Details(),
        )
        result = _responses_output_to_anthropic(resp)
        assert result.stop_reason == "max_tokens"


# ---------------------------------------------------------------------------
# End-to-end (still no network): adapter.messages.create stubbed at the OpenAI client
# ---------------------------------------------------------------------------


class TestAdapterMessagesCreateWiring:
    """Mock the underlying OpenAI client so we can assert the request kwargs
    the adapter assembles ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â the actual /v1/responses POST is never made."""

    def test_create_forwards_system_as_instructions(self, monkeypatch) -> None:
        captured: dict = {}

        class _StubResponses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _StubResponse(output=[
                    _StubItem(type="message",
                              content=[_StubSub(type="output_text", text="hi")]),
                ])

        class _StubClient:
            responses = _StubResponses()
            base_url = "https://api.perplexity.ai/v1"

        adapter = PerplexityResponsesAdapter.__new__(PerplexityResponsesAdapter)
        adapter._client = _StubClient()
        from debugger.perplexity_adapter import _PerplexityMessagesNamespace
        adapter.messages = _PerplexityMessagesNamespace(
            adapter._client, "anthropic/claude-sonnet-4-5",
        )

        adapter.messages.create(
            system="you are terse",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
            temperature=0.1,
            seed=42,             # accepted-but-ignored
            thinking={"x": 1},   # accepted-but-ignored
        )
        assert captured["instructions"] == "you are terse"
        assert captured["max_output_tokens"] == 8
        assert "seed" not in captured       # Responses API doesn't accept it
        assert "thinking" not in captured   # ditto
        # The input array must carry the user message.
        assert captured["input"][0]["role"] == "user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
