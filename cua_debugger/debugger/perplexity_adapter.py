"""
Perplexity adapter — wraps the ``/v1/responses`` endpoint and presents the
familiar Anthropic-style ``client.messages.create(...)`` interface.

Why this exists.  Perplexity is the only proxy in our supported list that
does NOT expose Anthropic models on the standard ``/v1/chat/completions``
endpoint; it serves them only via OpenAI's Responses API (``/v1/responses``).
Consequently the chat-completions-shaped ``OpenAICompatAdapter`` returns
404 against Perplexity. This adapter does the schema translation so RCA
and distill code can keep calling ``client.messages.create(...)`` without
caring about the underlying transport.

Public surface — exactly the methods the rest of the codebase relies on:

    client = PerplexityResponsesAdapter(model="anthropic/claude-sonnet-4-5",
                                        api_key=..., base_url=...)
    response = client.messages.create(model=..., system=..., messages=...,
                                      tools=..., max_tokens=..., timeout=...)

``response`` is an ``AnthropicResponse`` with the same ``content`` /
``stop_reason`` fields that the existing OpenAI-compat adapter emits.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Iterable, List

from openai import OpenAI

from debugger.together_adapter import (
    AnthropicResponse,
    TextBlock,
    ToolUseBlock,
)


# ---------------------------------------------------------------------------
# Anthropic -> Responses API schema translation
# ---------------------------------------------------------------------------


def _blk_field(block: Any, field: str, default: Any = None) -> Any:
    """Read a block's field from either a dataclass-like object or a dict."""
    if isinstance(block, dict):
        return block.get(field, default)
    return getattr(block, field, default)


def _anthropic_msgs_to_responses_input(
    messages: Iterable[dict],
) -> List[dict]:
    """Translate Anthropic-format messages into the Responses API ``input`` list.

    Responses API input items we emit (the only four we need):

    * ``{"type":"message","role":"user","content":[{"type":"input_text","text":...}]}``
    * ``{"type":"message","role":"assistant","content":[{"type":"output_text","text":...}]}``
    * ``{"type":"function_call","call_id":...,"name":...,"arguments":<json-string>}``
    * ``{"type":"function_call_output","call_id":...,"output":<string>}``

    The system prompt is passed at the request level (``instructions=``),
    not inside the input list — it is therefore handled by the caller.
    """
    out: List[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Plain string content → single message item with input_text / output_text.
        if isinstance(content, str):
            text_type = "output_text" if role == "assistant" else "input_text"
            out.append({
                "type":    "message",
                "role":    role,
                "content": [{"type": text_type, "text": content}],
            })
            continue

        if not isinstance(content, list):
            # Unknown shape — coerce to string as last resort.
            text_type = "output_text" if role == "assistant" else "input_text"
            out.append({
                "type":    "message",
                "role":    role,
                "content": [{"type": text_type, "text": str(content)}],
            })
            continue

        # User message carrying tool_result blocks (RCA ReAct loop emits these).
        first = content[0] if content else None
        if (role == "user"
                and isinstance(first, dict)
                and first.get("type") == "tool_result"):
            # Perplexity Responses' ``function_call_output`` only accepts a
            # string ``output`` — it cannot hold image blocks.  So we (a) send
            # the text channel of each tool_result as the function_call_output,
            # (b) collect any image blocks into a follow-up ``user`` message
            # carrying ``input_image`` items.  This mirrors the equivalent
            # OpenAI-compat path in ``together_adapter._anthropic_msgs_to_openai``
            # and is required for the RCA ReAct loop's ``get_step_details``
            # tool (which returns text + 2 screenshots) to actually deliver
            # the screenshots to Perplexity-routed models like sonnet-4-5.
            images_in_results: list[dict] = []
            for tool_result in content:
                tr_content = tool_result.get("content", "")
                if isinstance(tr_content, list):
                    parts: list[str] = []
                    for inner in tr_content:
                        inner_type = (
                            inner.get("type") if isinstance(inner, dict)
                            else _blk_field(inner, "type")
                        )
                        if inner_type == "text":
                            parts.append(
                                inner.get("text", "")
                                if isinstance(inner, dict)
                                else _blk_field(inner, "text", "")
                            )
                        elif inner_type == "image":
                            src = (
                                inner.get("source", {}) if isinstance(inner, dict)
                                else (_blk_field(inner, "source", {}) or {})
                            ) or {}
                            b64 = src.get("data", "") if isinstance(src, dict) else ""
                            media = (
                                src.get("media_type", "image/png")
                                if isinstance(src, dict) else "image/png"
                            )
                            images_in_results.append({
                                "type":      "input_image",
                                "image_url": f"data:{media};base64,{b64}",
                            })
                            parts.append("[Screenshot attached — see follow-up image below]")
                    tr_content = "\n".join(p for p in parts if p)
                out.append({
                    "type":    "function_call_output",
                    "call_id": tool_result.get("tool_use_id", ""),
                    "output":  str(tr_content),
                })
            if images_in_results:
                out.append({
                    "type":    "message",
                    "role":    "user",
                    "content": images_in_results + [{
                        "type": "input_text",
                        "text": "Above is the screenshot(s) from the tool result. Continue your analysis.",
                    }],
                })
            continue

        # Assistant message with Anthropic content blocks (text, thinking, tool_use).
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for blk in content:
                btype = _blk_field(blk, "type")
                if btype == "text":
                    text_parts.append(_blk_field(blk, "text", "") or "")
                elif btype == "thinking":
                    # Drop thinking blocks; Perplexity doesn't surface them.
                    continue
                elif btype == "tool_use":
                    tool_calls.append({
                        "type":      "function_call",
                        "call_id":   _blk_field(blk, "id", "") or "",
                        "name":      _blk_field(blk, "name", "") or "",
                        "arguments": json.dumps(_blk_field(blk, "input", {}) or {}),
                    })

            joined_text = "\n".join(t for t in text_parts if t)
            if joined_text:
                out.append({
                    "type":    "message",
                    "role":    "assistant",
                    "content": [{"type": "output_text", "text": joined_text}],
                })
            out.extend(tool_calls)
            continue

        # User message with structured content list (text + optional images).
        # Translate Anthropic image blocks → Responses-API ``input_image``
        # blocks so Perplexity-routed models (e.g. anthropic/claude-sonnet-4-5)
        # actually receive the screenshots instead of a "[Screenshot
        # attached]" text placeholder.
        if role == "user":
            inner_parts: list[dict] = []
            for inner in content:
                inner_type = _blk_field(inner, "type")
                if inner_type == "text":
                    inner_parts.append({
                        "type": "input_text",
                        "text": _blk_field(inner, "text", "") or "",
                    })
                elif inner_type == "image":
                    src = _blk_field(inner, "source", {}) or {}
                    b64 = src.get("data", "") if isinstance(src, dict) else ""
                    media = src.get("media_type", "image/png") if isinstance(src, dict) else "image/png"
                    inner_parts.append({
                        "type":      "input_image",
                        "image_url": f"data:{media};base64,{b64}",
                    })
            out.append({
                "type":    "message",
                "role":    "user",
                "content": inner_parts or [{"type": "input_text", "text": ""}],
            })

    return out


def _anthropic_tools_to_responses(tools: Iterable[dict]) -> List[dict]:
    """Convert Anthropic tool descriptors to Responses API tool descriptors.

    Anthropic: ``{"name": ..., "description": ..., "input_schema": {...}}``
    Responses: ``{"type": "function", "name": ..., "description": ...,
                 "parameters": {...}}`` (flat — no ``function`` wrapper, no
                 ``strict`` requirement).
    """
    result: List[dict] = []
    for tool in tools:
        result.append({
            "type":        "function",
            "name":        tool["name"],
            "description": tool.get("description", ""),
            "parameters":  tool.get("input_schema",
                                    {"type": "object", "properties": {}}),
        })
    return result


# ---------------------------------------------------------------------------
# Response -> Anthropic schema translation
# ---------------------------------------------------------------------------


def _responses_output_to_anthropic(response: Any) -> AnthropicResponse:
    """Convert a Responses API response object into an AnthropicResponse.

    Responses ``output`` is a list of typed items.  We care about:

    * ``message``      — assistant text (one or more text outputs per item).
    * ``function_call`` — a tool the assistant wants to invoke.

    ``stop_reason`` is set to ``"tool_use"`` if any function_call appears,
    otherwise ``"end_turn"``.  ``max_tokens`` is reported when the response
    object indicates an output-length cutoff.
    """
    content_blocks: list = []
    stop_reason = "end_turn"

    output_items = getattr(response, "output", None) or []
    for item in output_items:
        item_type = getattr(item, "type", None)

        if item_type == "message":
            # ``item.content`` is itself a list of output_text / refusal items.
            for sub in (getattr(item, "content", None) or []):
                sub_type = getattr(sub, "type", None)
                text = getattr(sub, "text", None)
                if sub_type == "output_text" and text:
                    content_blocks.append(TextBlock(text=text))

        elif item_type == "function_call":
            stop_reason = "tool_use"
            raw_args = getattr(item, "arguments", "") or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                parsed_args = {}
            content_blocks.append(ToolUseBlock(
                id=(getattr(item, "call_id", None)
                    or f"toolu_{uuid.uuid4().hex[:12]}"),
                name=getattr(item, "name", "") or "",
                input=parsed_args,
            ))

    # Honour an output-length cutoff if Perplexity reports one.
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None) if details else None
        if reason == "max_output_tokens":
            stop_reason = "max_tokens"

    return AnthropicResponse(content=content_blocks, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# Adapter shell + ``messages.create`` namespace
# ---------------------------------------------------------------------------


class _PerplexityMessagesNamespace:
    """Mimics ``client.messages`` so callers can keep using ``.create()``."""

    def __init__(self, openai_client: OpenAI, default_model: str) -> None:
        self._client = openai_client
        self._default_model = default_model

    def create(
        self,
        model: str = "",
        max_tokens: int = 4096,
        system: str = "",
        tools: list[dict] | None = None,
        messages: list[dict] | None = None,
        thinking: dict | None = None,   # ignored — Perplexity hides it
        temperature: float = 0.1,
        seed: int = 42,                 # ignored — Responses API rejects seed
        timeout: int | None = None,
        **kwargs,
    ) -> AnthropicResponse:
        """Issue one Responses-API call and return an Anthropic-style response.

        ``seed`` and ``thinking`` are accepted-and-ignored so the rest of the
        codebase can call us with the same kwargs it sends to the OpenAI-
        compat adapter.  ``temperature`` is forwarded.  ``system`` is sent
        as Responses-API ``instructions``.
        """
        chosen_model = model or self._default_model

        responses_input = _anthropic_msgs_to_responses_input(messages or [])
        responses_tools = _anthropic_tools_to_responses(tools) if tools else None

        call_kwargs: dict[str, Any] = {
            "model":             chosen_model,
            "input":             responses_input,
            "max_output_tokens": max_tokens,
            "temperature":       temperature,
        }
        if system:
            call_kwargs["instructions"] = system
        if responses_tools:
            call_kwargs["tools"] = responses_tools
        if timeout is not None:
            call_kwargs["timeout"] = timeout

        response = self._client.responses.create(**call_kwargs)
        return _responses_output_to_anthropic(response)


class PerplexityResponsesAdapter:
    """Drop-in replacement for ``anthropic.Anthropic()`` aimed at Perplexity.

    Only ``client.messages.create()`` is implemented; that is enough for the
    RCA, distill, intention and discussion code paths in this project.
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        key = (
            api_key
            or os.environ.get("SONNET_API_KEY", "")
            or os.environ.get("PERPLEXITY_API_KEY", "")
        )
        url = base_url or os.environ.get("SONNET_BASE_URL", "")
        if not url:
            raise ValueError(
                "Perplexity base URL required. "
                "Set SONNET_BASE_URL env var or pass base_url=."
            )
        self._client = OpenAI(api_key=key, base_url=url, max_retries=3)
        self.messages = _PerplexityMessagesNamespace(self._client, model)
