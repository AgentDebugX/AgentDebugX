"""`OpenAICompatClient(default_headers=...)` — sent on every request, never able to break auth.

Attribution is the worst case for gateway prompt caching: the trajectory is a long shared
prefix, and a bisecting or ensemble attributor sends it several times. A gateway fronting a pool
of upstream keys load-balances each request independently, so without a session header the
second call lands on a different key and re-prefills everything. There was previously no way to
set such a header on this client.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from agentdebug.runtime.llm import OpenAICompatClient


class _Recorder:
    """Captures the headers of every httpx.post the client makes."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, headers=None, json=None, timeout=None):  # noqa: A002
        self.calls.append({'url': url, 'headers': dict(headers or {})})

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
                }

            @staticmethod
            def raise_for_status():
                return None

            text = ''

        return _Resp()


def _client(**kw) -> OpenAICompatClient:
    return OpenAICompatClient(base_url='https://example.invalid/v1', api_key='k',
                              model='m', **kw)


def test_default_is_none_so_existing_callers_are_unaffected():
    assert _client().default_headers == {}


def test_a_supplied_header_reaches_the_request(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr('agentdebug.runtime.llm.httpx', rec)
    c = _client(default_headers={'X-Llmhub-Session': 'trace-abc'})
    c.complete([{'role': 'user', 'content': 'hi'}])
    assert rec.calls, 'no request was made'
    assert rec.calls[0]['headers']['X-Llmhub-Session'] == 'trace-abc'


def test_auth_and_content_type_cannot_be_overridden(monkeypatch):
    """A caller must not be able to break authentication with a typo in a header name."""
    rec = _Recorder()
    monkeypatch.setattr('agentdebug.runtime.llm.httpx', rec)
    c = _client(default_headers={'Authorization': 'Bearer WRONG',
                                 'Content-Type': 'text/plain'})
    c.complete([{'role': 'user', 'content': 'hi'}])
    h = rec.calls[0]['headers']
    assert h['Authorization'] == 'Bearer k'
    assert h['Content-Type'] == 'application/json'


def test_the_mapping_is_copied_not_aliased():
    """Mutating the caller's dict afterwards must not change what the client sends."""
    supplied = {'X-Llmhub-Session': 'one'}
    c = _client(default_headers=supplied)
    supplied['X-Llmhub-Session'] = 'two'
    assert c.default_headers['X-Llmhub-Session'] == 'one'


@pytest.mark.parametrize('value', [{}, None])
def test_empty_and_none_are_equivalent(value):
    assert _client(default_headers=value).default_headers == {}
