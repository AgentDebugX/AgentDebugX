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
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import httpx

LOG = logging.getLogger('agentdebug.llm')


@dataclass
class TokenUsage:
    """Tokens billed for one call, or accumulated over many.

    Every OpenAI-compatible endpoint already returns this in ``raw['usage']``; before this
    existed the information reached ``CompletionResult.raw`` and stopped there, so a caller
    driving thousands of attributions had no way to bill them. Downstream cost tables
    reported 0.0 for every AgentDebugX diagnosis, which makes the cheap and the expensive
    attributor look identical.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            calls=self.calls + other.calls,
            cost_usd=round(self.cost_usd + other.cost_usd, 8),
        )

    @classmethod
    def from_response(
        cls,
        raw: Dict[str, Any],
        *,
        price_in: float = 0.0,
        price_out: float = 0.0,
    ) -> 'TokenUsage':
        """Parse ``raw['usage']``, tolerating gateways that omit or rename fields.

        Prices are USD per 1M tokens. Left at 0.0 the usage is still counted, so token
        accounting works even when a gateway bills opaquely and only the token counts are
        trustworthy.
        """
        usage = raw.get('usage') or {}
        prompt = int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0)
        completion = int(
            usage.get('completion_tokens') or usage.get('output_tokens') or 0
        )
        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            calls=1,
            cost_usd=round(prompt * price_in / 1e6 + completion * price_out / 1e6, 8),
        )


@dataclass
class CompletionResult:
    text: str
    raw: Dict[str, Any]
    #: Defaulted so every existing construction site keeps working unchanged.
    usage: TokenUsage = field(default_factory=TokenUsage)


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
        price_in: float = 0.0,
        price_out: float = 0.0,
        on_usage: Optional[Any] = None,
        max_retries: int = 0,
        retry_base_delay: float = 1.0,
        default_headers: Optional[Dict[str, str]] = None,

    ) -> None:
        self.base_url = base_url.rstrip('/')
        #: Retries on 429, 5xx and transport errors. Off by default so an existing
        #: caller sees exactly the failures it always saw; a batch runner against a
        #: shared gateway turns it on rather than losing a trajectory to one 503.
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.api_key = api_key
        self.model = model
        # Embeddings hit a separate endpoint with a separate model id; default
        # to OpenAI's small embedding model since the gateway is OpenAI-compat.
        self.embedding_model = embedding_model
        self.default_max_tokens = default_max_tokens
        self.timeout = timeout
        self.extra_body = extra_body
        #: Headers added to every request this client makes. Defaulted to None, so existing
        #: callers and third-party subclasses are unaffected.
        #:
        #: WHY THIS IS NEEDED. A gateway that fronts a pool of upstream keys load-balances each
        #: request independently, so the second call of a conversation usually lands on a
        #: different key and re-prefills the entire prompt. Such gateways expose a session
        #: header to pin one conversation to one key.
        #:
        #: Attribution is the worst case for that. The trajectory is a long shared prefix, and
        #: a bisecting or ensemble attributor sends it several times; without a way to set the
        #: header, every call after the first misses the cache. The effect is large and
        #: invisible from the outside -- one gateway operator measured 4-5% cache hit rate on a
        #: 9-key channel without the header against 82-96% with it.
        #:
        #: `Authorization` and `Content-Type` are applied AFTER this mapping and therefore
        #: cannot be overridden by it, so a caller cannot accidentally break authentication.
        self.default_headers = dict(default_headers or {})
        # USD per 1M tokens. Optional: token counts are accumulated regardless, so a
        # gateway that bills opaquely still yields usable usage data.
        self.price_in = price_in
        self.price_out = price_out
        #: Cumulative usage over the client's lifetime. Read it after a batch to learn what
        #: that batch cost without threading a meter through every attributor.
        self.usage_total = TokenUsage()
        #: Optional callback invoked with each call's TokenUsage, so an external cost meter
        #: or budget gate can observe spend as it happens rather than after the fact.
        self._on_usage = on_usage

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
            **self.default_headers,
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        resp = self._post(url, headers, body, timeout or self.timeout)
        if (
            resp.status_code == 400
            and token_param == 'max_tokens'
            and 'max_completion_tokens' in resp.text
        ):
            self._token_param = 'max_completion_tokens'
            body.pop('max_tokens', None)
            body['max_completion_tokens'] = token_budget
            resp = self._post(url, headers, body, timeout or self.timeout)
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
        return CompletionResult(text=text, raw=data, usage=self._record_usage(data))

    #: Statuses worth retrying: rate limit, and the gateway-side failures a shared
    #: proxy produces under load. A 4xx other than 429 is the caller's mistake and
    #: retrying it only delays the traceback.
    _RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

    def _post(
        self,
        url: str,
        headers: Dict[str, str],
        body: Dict[str, Any],
        timeout: float,
    ) -> Any:
        """``httpx.post`` with opt-in retry on transient failures.

        With ``max_retries=0`` this is exactly one ``httpx.post`` and every
        exception propagates unchanged. Otherwise a retryable status or a
        transport error is retried with exponential backoff and jitter, and a
        ``Retry-After`` header is honoured when the server sends one.
        """
        attempt = 0
        while True:
            try:
                resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self._retry_delay(attempt, None)
                LOG.warning('transport error from %s (%s); retry %d/%d in %.1fs',
                            url, exc, attempt + 1, self.max_retries, delay)
            else:
                if resp.status_code not in self._RETRY_STATUSES or attempt >= self.max_retries:
                    return resp
                delay = self._retry_delay(attempt, resp.headers.get('retry-after'))
                LOG.warning('HTTP %d from %s; retry %d/%d in %.1fs',
                            resp.status_code, url, attempt + 1, self.max_retries, delay)
            time.sleep(delay)
            attempt += 1

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        base = self.retry_base_delay * (2 ** attempt)
        return min(60.0, base + random.uniform(0, base))

    def _record_usage(self, data: Dict[str, Any]) -> TokenUsage:
        """Parse, accumulate and publish the usage for one response."""
        usage = TokenUsage.from_response(
            data, price_in=self.price_in, price_out=self.price_out
        )
        self.usage_total = self.usage_total + usage
        if self._on_usage is not None:
            try:
                self._on_usage(usage)
            except Exception:  # pragma: no cover
                # A misbehaving meter must never lose a completion the caller paid for.
                LOG.warning('on_usage callback raised; usage still accumulated')
        return usage

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
            **self.default_headers,
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
        # chat() returns the raw choice, so the usage has to be accumulated here as well —
        # otherwise every tool-calling attributor bills invisibly. Return value unchanged.
        self._record_usage(data)
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
            **self.default_headers,
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
