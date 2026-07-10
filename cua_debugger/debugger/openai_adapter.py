"""
Generic OpenAI-compatible adapter — provides an Anthropic-style client interface
backed by any OpenAI-compatible chat-completions endpoint.

Works with: OpenAI itself, Azure OpenAI, OpenRouter, Together (chat-completions
mode), self-hosted vLLM, the Gemini OpenAI-compatible proxy, etc. The endpoint
is determined entirely by ``base_url`` + ``api_key``; the model name is passed
through verbatim.

Usage:
    from debugger.openai_adapter import OpenAICompatAdapter

    client = OpenAICompatAdapter(model="gpt-4o")
    # Now pass `client` wherever an `Anthropic()` client is expected (rca.py).

Environment variables: each provider name has its own key env. Caller is
responsible for resolving and passing it. e.g. provider='gemini' uses
GEMINI_API_KEY, provider='azure' uses AZURE_API_KEY, etc.
"""

import json
import os
import uuid
from typing import Any

from openai import OpenAI

from debugger.together_adapter import (
    AnthropicResponse,
    TextBlock,
    ToolUseBlock,
    _anthropic_msgs_to_openai,
    _anthropic_tools_to_openai,
)


class _OpenAIMessagesNamespace:
    """Mimics `client.messages` with a `.create()` method."""

    def __init__(self, openai_client: OpenAI, default_model: str):
        self._client = openai_client
        self._default_model = default_model

    def create(
        self,
        model: str = "",
        max_tokens: int = 4096,
        system: str = "",
        tools: list[dict] | None = None,
        messages: list[dict] | None = None,
        thinking: dict | None = None,  # ignored — most OpenAI-compat endpoints don't expose it
        temperature: float = 0.1,      # determinism default for the whole pipeline
        seed: int = 42,                # determinism default for the whole pipeline
        timeout: int | None = None,    # per-request timeout in seconds (None = SDK default)
        **kwargs,
    ) -> AnthropicResponse:
        """
        Issue one OpenAI-compatible chat completion and convert to an
        Anthropic-style response.

        ``temperature`` / ``seed`` default to the pipeline-wide determinism
        settings so every LLM call (trajectory → distill → memory → RCA)
        inherits them unless a caller explicitly overrides.
        """
        model = model or self._default_model

        openai_msgs = _anthropic_msgs_to_openai(messages or [], system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        call_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": openai_msgs,
            "temperature": temperature,
            "seed": seed,
        }
        if openai_tools:
            call_kwargs["tools"] = openai_tools
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        response = self._client.chat.completions.create(**call_kwargs)

        choice = response.choices[0]
        content_blocks: list = []
        stop_reason = "end_turn"

        if choice.message.content:
            content_blocks.append(TextBlock(text=choice.message.content))

        if choice.message.tool_calls:
            stop_reason = "tool_use"
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                content_blocks.append(ToolUseBlock(
                    id=tc.id or f"toolu_{uuid.uuid4().hex[:12]}",
                    name=tc.function.name,
                    input=args,
                ))

        if choice.finish_reason == "tool_calls":
            stop_reason = "tool_use"

        return AnthropicResponse(content=content_blocks, stop_reason=stop_reason)


class OpenAICompatAdapter:
    """
    Drop-in replacement for `anthropic.Anthropic()` that routes to any
    OpenAI-compatible chat-completions endpoint.

    Only implements `client.messages.create()` — enough for rca.py.
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
    ):
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        if not url:
            raise ValueError(
                "OpenAI-compatible base URL required. "
                "Set OPENAI_BASE_URL env var or pass base_url=."
            )
        # max_retries=3: let the OpenAI SDK honor `retry-after` on 429 / 5xx (the SDK
        # parses the header and waits the right amount). 0 = our previous setting; we
        # had it disabled which caused 429 storms on low-tier accounts. 3 is the SDK default.
        self._client = OpenAI(api_key=key, base_url=url, max_retries=3)
        self.messages = _OpenAIMessagesNamespace(self._client, model)
