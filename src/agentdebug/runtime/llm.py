"""Thin LLM client abstraction.

AgentDebugX needs an LLM for the judge analyzer and for the All-at-Once
attributor. We use an OpenAI-compatible chat-completions interface so users can
point us at OpenAI, Anthropic via LiteLLM, the Gemini endpoint they hand us, or
a local vLLM/Ollama deployment.

The implementation deliberately avoids depending on the ``openai`` Python SDK:
a single ``httpx`` POST keeps the install lightweight and lets users target any
``/v1/chat/completions``-compatible URL.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

import httpx

LOG = logging.getLogger('agentdebug.llm')


@dataclass
class CompletionResult:
    text: str
    raw: Dict[str, Any]


class LLMClient(Protocol):
    model: str

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> CompletionResult:
        ...


class EmbeddingClient(Protocol):
    """Subprotocol for clients that also expose ``/v1/embeddings``.

    Kept separate from :class:`LLMClient` so detectors can declare a
    narrower dependency and tests can stub embeddings without faking
    a chat client.
    """

    embedding_model: str

    def embed(
        self,
        texts: List[str],
        *,
        timeout: float = 60.0,
    ) -> List[List[float]]:
        ...


class OpenAICompatClient:
    """OpenAI-compatible chat completions client.

    Works against:

    * OpenAI (``base_url='https://api.openai.com/v1'``)
    * LiteLLM proxy (any ``/v1`` URL)
    * Gemini or other hosted gateways exposed through an OpenAI-compatible
      ``/v1`` endpoint, with a matching model name.
    * vLLM / Ollama with their OpenAI-compat servers
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        embedding_model: str = 'text-embedding-3-small',
        default_max_tokens: int = 2048,
        timeout: float = 60.0,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        # Embeddings hit a separate endpoint with a separate model id; default
        # to OpenAI's small embedding model since the gateway is OpenAI-compat.
        self.embedding_model = embedding_model
        self.default_max_tokens = default_max_tokens
        self.timeout = timeout
        self.extra_body = extra_body

    @classmethod
    def from_env(
        cls,
        *,
        env_prefix: str = 'AGENTDEBUG_LLM',
        model: Optional[str] = None,
    ) -> 'OpenAICompatClient':
        """Construct from environment variables.

        Reads ``<PREFIX>_BASE_URL``, ``<PREFIX>_API_KEY``, ``<PREFIX>_MODEL``.
        """
        base_url = os.environ[f'{env_prefix}_BASE_URL']
        api_key = os.environ[f'{env_prefix}_API_KEY']
        model_id: str = (
            model if model is not None
            else os.environ.get(f'{env_prefix}_MODEL', 'gpt-4o-mini')
        )
        return cls(base_url=base_url, api_key=api_key, model=model_id)

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> CompletionResult:
        token_budget = max_tokens or self.default_max_tokens
        # Newer OpenAI reasoning models (gpt-5*, o-series) reject 'max_tokens'
        # and require 'max_completion_tokens'. Remember the working key per
        # client so we only pay the extra round-trip once.
        token_param = getattr(self, '_token_param', 'max_tokens')
        body: Dict[str, Any] = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            token_param: token_budget,
        }
        if response_format is not None:
            body['response_format'] = response_format
        if self.extra_body:
            body.update(self.extra_body)
        url = f'{self.base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        resp = httpx.post(
            url, headers=headers, json=body, timeout=timeout or self.timeout
        )
        if (
            resp.status_code == 400
            and token_param == 'max_tokens'
            and 'max_completion_tokens' in resp.text
        ):
            self._token_param = 'max_completion_tokens'
            body.pop('max_tokens', None)
            body['max_completion_tokens'] = token_budget
            resp = httpx.post(
                url, headers=headers, json=body, timeout=timeout or self.timeout
            )
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        choice = (data.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        text = message.get('content') or ''
        if not text:
            LOG.warning(
                'empty content from %s (model=%s, finish_reason=%s, usage=%s)',
                url,
                self.model,
                choice.get('finish_reason'),
                data.get('usage'),
            )
        return CompletionResult(text=text, raw=data)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        thinking: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Tool-capable chat completion returning the RAW first choice dict.

        Mirrors :meth:`complete` (same httpx-POST body/headers/error-logging)
        but additionally places ``tools`` / ``tool_choice`` / ``seed`` into the
        request body and returns ``data['choices'][0]`` so the caller can read
        both ``choice['message']['content']`` and ``choice['message']['tool_calls']``
        plus ``choice['finish_reason']``.

        ``thinking`` is accepted and IGNORED — OpenAI-compat backends don't
        expose it (matches CUA's own provider adapters).
        """
        _ = thinking  # accepted for the Anthropic-style channel; not sent
        body: Dict[str, Any] = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens or self.default_max_tokens,
        }
        if tools:
            body['tools'] = tools
        if tool_choice is not None:
            body['tool_choice'] = tool_choice
        if seed is not None:
            body['seed'] = seed
        if self.extra_body:
            body.update(self.extra_body)
        url = f'{self.base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        resp = httpx.post(
            url, headers=headers, json=body, timeout=timeout or self.timeout
        )
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        choice = (data.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        if not (message.get('content') or message.get('tool_calls')):
            LOG.warning(
                'empty content/tool_calls from %s (model=%s, finish_reason=%s, usage=%s)',
                url,
                self.model,
                choice.get('finish_reason'),
                data.get('usage'),
            )
        return choice

    def embed(
        self,
        texts: List[str],
        *,
        timeout: Optional[float] = None,
    ) -> List[List[float]]:
        """OpenAI-compatible ``/v1/embeddings`` POST.

        Returns a list of vectors (one per input text) in the same order.
        Empty ``texts`` short-circuits to ``[]`` to save a network round-trip.
        """
        if not texts:
            return []
        url = f'{self.base_url}/embeddings'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        body = {'model': self.embedding_model, 'input': list(texts)}
        resp = httpx.post(
            url, headers=headers, json=body, timeout=timeout or self.timeout
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get('data') or []
        out: List[List[float]] = []
        for row in rows:
            vec = row.get('embedding')
            if not isinstance(vec, list):
                continue
            out.append([float(v) for v in vec])
        return out


def extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extract the first top-level JSON object from a possibly-fenced response."""
    if not text:
        return None
    # Strip code fences if present.
    cleaned = text.strip()
    if cleaned.startswith('```'):
        # remove ``` or ```json prefix and trailing ```
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
        if cleaned.endswith('```'):
            cleaned = cleaned[: -len('```')]
        cleaned = cleaned.strip()
    # Try strict first.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fallback: greedy slice between the first { and the last }.
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None
    return None
