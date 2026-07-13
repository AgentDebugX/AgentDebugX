"""RCA-03 channel adapter: an Anthropic-style ``.messages.create(...)`` seam
that routes the network call through AgentDebugX ``core/llm.py``.

CUA's ``run_react_loop`` (``cua_debugger/debugger/agent.py``) drives any client
exposing ``client.messages.create(model, max_tokens, system, tools, messages,
thinking, timeout)`` and returning an object with ``.content`` (a list of
Anthropic-style blocks) + ``.stop_reason``. This module presents exactly that
interface but performs the actual completion via
:meth:`OpenAICompatClient.chat` — never through ``anthropic.Anthropic`` /
``openai.OpenAI`` / Together. That routing is what satisfies RCA-03.

The pure format-conversion helpers (Anthropic<->OpenAI messages/tools, incl. the
Anthropic-image -> OpenAI ``image_url`` vision path, and the Anthropic-style
block dataclasses) are REUSED from the vendored CUA tree
(``cua_debugger/debugger/together_adapter.py``) via a guarded lazy import — they
are translators, not provider clients, so reusing them does not violate RCA-03.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_cua_converters() -> Tuple[Any, Any, Any, Any, Any]:
    """Import the vendored CUA format converters lazily; clear error if absent.

    Resolves ``<repo>/cua_debugger`` onto ``sys.path`` at call time (only when
    the directory exists and is not already present) and imports the pure
    translators. Never imports ``debugger`` at module top level so
    ``import agentdebug`` stays free of the vendored tree.
    """
    cua_root = Path(__file__).resolve().parents[3] / 'cua_debugger'
    if cua_root.is_dir() and str(cua_root) not in sys.path:
        sys.path.insert(0, str(cua_root))
    try:
        from debugger.together_adapter import (
            AnthropicResponse,
            TextBlock,
            ToolUseBlock,
            _anthropic_msgs_to_openai,
            _anthropic_tools_to_openai,
        )
    except ImportError as exc:
        raise ImportError(
            'GUI RCA channel requires the vendored CUA source tree '
            '(cua_debugger) on sys.path. Add cua_debugger/ to your PYTHONPATH.'
        ) from exc
    return (
        AnthropicResponse,
        TextBlock,
        ToolUseBlock,
        _anthropic_msgs_to_openai,
        _anthropic_tools_to_openai,
    )


class _CoreLLMMessagesNamespace:
    """Mimics ``client.messages`` with a ``.create()`` method backed by
    :meth:`OpenAICompatClient.chat`."""

    def __init__(self, client: Any, default_model: str) -> None:
        self._client = client
        self._default_model = default_model

    def create(
        self,
        model: str = '',
        max_tokens: int = 4096,
        system: str = '',
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        thinking: Optional[Dict[str, Any]] = None,  # ignored — no OpenAI-compat exposure
        temperature: float = 0.1,
        seed: int = 42,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> Any:
        (
            AnthropicResponse,
            TextBlock,
            ToolUseBlock,
            _anthropic_msgs_to_openai,
            _anthropic_tools_to_openai,
        ) = _load_cua_converters()

        openai_msgs = _anthropic_msgs_to_openai(messages or [], system)
        openai_tools = _anthropic_tools_to_openai(tools) if tools else None

        # RCA-03 seam: the network call goes through core/llm.py, NOT a
        # provider SDK. ``model`` from the loop is informational only — the
        # injected OpenAICompatClient owns the resolved model id.
        choice = self._client.chat(
            openai_msgs,
            tools=openai_tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            thinking=thinking,
            seed=seed,
        )

        message = choice.get('message') or {}
        content_blocks: list = []
        stop_reason = 'end_turn'

        content = message.get('content')
        if content:
            content_blocks.append(TextBlock(text=content))

        tool_calls = message.get('tool_calls')
        if tool_calls:
            stop_reason = 'tool_use'
            for tc in tool_calls:
                func = tc.get('function') or {}
                try:
                    args = json.loads(func.get('arguments') or '{}')
                except (json.JSONDecodeError, TypeError):
                    args = {}
                content_blocks.append(
                    ToolUseBlock(
                        id=tc.get('id') or f'toolu_{uuid.uuid4().hex[:12]}',
                        name=func.get('name', ''),
                        input=args,
                    )
                )

        if choice.get('finish_reason') == 'tool_calls':
            stop_reason = 'tool_use'

        return AnthropicResponse(content=content_blocks, stop_reason=stop_reason)


class CoreLLMChannel:
    """Drop-in replacement for ``anthropic.Anthropic()`` that routes to
    AgentDebugX ``core/llm.py``.

    Only implements ``client.messages.create()`` — enough for CUA's
    ``run_react_loop`` / ``run_rca``.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self.messages = _CoreLLMMessagesNamespace(
            client, getattr(client, 'model', '')
        )

    @classmethod
    def from_env(cls, *, model: Optional[str] = None) -> 'CoreLLMChannel':
        """Build from ``AGENTDEBUG_LLM_*`` via :meth:`OpenAICompatClient.from_env`."""
        from agentdebug.runtime.llm import OpenAICompatClient

        return cls(OpenAICompatClient.from_env(model=model))
