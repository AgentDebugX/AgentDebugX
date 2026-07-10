"""
Together AI adapter — provides an Anthropic-compatible client interface
backed by Together AI's OpenAI-compatible API.

Usage:
    from debugger.together_adapter import TogetherAnthropicAdapter

    client = TogetherAnthropicAdapter(model="Qwen/Qwen2.5-72B-Instruct")
    # Now pass `client` wherever `Anthropic()` client is expected in rca.py
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# `from together import Together` is imported lazily inside TogetherAnthropicAdapter
# so users on the openai provider don't need a working `together` package install.


# ── Anthropic-compatible response types ────────────────────────────────────

@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""

@dataclass
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

@dataclass
class AnthropicResponse:
    content: list = field(default_factory=list)
    stop_reason: str = ""


# ── Tool schema → function-calling conversion ─────────────────────────────

def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format."""
    result = []
    for tool in tools:
        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            }
        })
    return result


def _anthropic_msgs_to_openai(messages: list[dict], system: str = "") -> list[dict]:
    """
    Convert Anthropic message format to OpenAI format.
    Handles:
      - system prompt → system message
      - user text → user message
      - assistant with Anthropic content blocks → assistant + tool_calls
      - user with tool_result blocks → tool messages
    """
    out = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")

        # Simple string content
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # List content — Anthropic format
        if isinstance(content, list):
            # Check if it's tool_result blocks (user role)
            if role == "user" and content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                for tr in content:
                    tool_content = tr.get("content", "")
                    # Content could be a list of blocks
                    if isinstance(tool_content, list):
                        parts = []
                        for blk in tool_content:
                            if isinstance(blk, dict) and blk.get("type") == "text":
                                parts.append(blk["text"])
                            elif isinstance(blk, dict) and blk.get("type") == "image":
                                parts.append("[Screenshot attached]")
                        tool_content = "\n".join(parts) if parts else str(tool_content)
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": str(tool_content),
                    })

                # Extract images from tool results and send as a follow-up user message
                images_in_results = []
                for tr in content:
                    tc_raw = tr.get("content", "")
                    if isinstance(tc_raw, list):
                        for blk in tc_raw:
                            if isinstance(blk, dict) and blk.get("type") == "image":
                                src = blk.get("source", {})
                                b64 = src.get("data", "")
                                media = src.get("media_type", "image/png")
                                images_in_results.append({
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{media};base64,{b64}"}
                                })
                if images_in_results:
                    out.append({
                        "role": "user",
                        "content": images_in_results + [
                            {"type": "text", "text": "Above is the screenshot from the tool result. Continue your analysis."}
                        ],
                    })
                continue

            # User content list of mixed text + image blocks (e.g. run_plain
            # attaching the last-N screenshots).  Translate Anthropic image
            # blocks to OpenAI's vision-format content parts so providers
            # like Gemini-OpenAI-compat and Qwen actually receive the images
            # rather than the str(content) garbage the fallback emits.
            if role == "user":
                parts: list[dict] = []
                for blk in content:
                    if not isinstance(blk, dict):
                        continue
                    btype = blk.get("type")
                    if btype == "text":
                        parts.append({"type": "text", "text": blk.get("text", "")})
                    elif btype == "image":
                        src = blk.get("source", {})
                        b64 = src.get("data", "")
                        media = src.get("media_type", "image/png")
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media};base64,{b64}"},
                        })
                if parts:
                    out.append({"role": "user", "content": parts})
                    continue
                # No recognised blocks; fall through to the stringify fallback.

            # Assistant content blocks (may include text, thinking, tool_use)
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for blk in content:
                    # Handle both dataclass objects and dicts
                    btype = getattr(blk, "type", None) or (blk.get("type") if isinstance(blk, dict) else None)

                    if btype == "text":
                        t = getattr(blk, "text", None) or (blk.get("text", "") if isinstance(blk, dict) else "")
                        text_parts.append(t)
                    elif btype == "thinking":
                        pass  # skip thinking blocks
                    elif btype == "tool_use":
                        name = getattr(blk, "name", None) or blk.get("name", "")
                        inp = getattr(blk, "input", None) or blk.get("input", {})
                        bid = getattr(blk, "id", None) or blk.get("id", "")
                        tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(inp),
                            }
                        })

                # DashScope's /compatible-mode rejects content=None on assistant
                # messages even when tool_calls is present; use "" instead — OpenAI,
                # Gemini-compat, and Perplexity all accept an empty string fine.
                assistant_msg = {"role": "assistant", "content": "\n".join(text_parts)}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
                continue

            # Fallback: just stringify
            out.append({"role": role, "content": str(content)})

    return out


# ── Adapter ────────────────────────────────────────────────────────────────

class _MessagesNamespace:
    """Mimics `client.messages` with a `.create()` method."""

    def __init__(self, together_client: "Together", default_model: str):  # type: ignore[name-defined]  # lazy import
        self._client = together_client
        self._default_model = default_model

    def create(
        self,
        model: str = "",
        max_tokens: int = 4096,
        system: str = "",
        tools: list[dict] | None = None,
        messages: list[dict] | None = None,
        thinking: dict | None = None,  # ignored — Together doesn't support extended thinking
        temperature: float = 0.1,      # determinism default for the whole pipeline
        seed: int = 42,                # determinism default for the whole pipeline
        timeout: int | None = None,    # per-request timeout in seconds (None = SDK default)
        **kwargs,
    ) -> AnthropicResponse:
        """
        Issue one Together (OpenAI-compatible) chat completion and convert to
        an Anthropic-style response.

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

        # Convert OpenAI response → Anthropic-compatible response
        choice = response.choices[0]
        content_blocks: list = []
        stop_reason = "end_turn"

        # Text content
        if choice.message.content:
            content_blocks.append(TextBlock(text=choice.message.content))

        # Tool calls
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


class TogetherAnthropicAdapter:
    """
    Drop-in replacement for `anthropic.Anthropic()` that routes to Together AI.

    Only implements `client.messages.create()` — enough for rca.py.
    """

    def __init__(self, model: str = "Qwen/Qwen2.5-72B-Instruct", api_key: str = ""):
        from together import Together  # lazy — only required when actually using Together
        key = api_key or os.environ.get("TOGETHER_API_KEY", "")
        self._client = Together(api_key=key)
        self.messages = _MessagesNamespace(self._client, model)
