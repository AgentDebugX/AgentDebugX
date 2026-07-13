from __future__ import annotations

from agentdebug.runtime.llm import OpenAICompatClient, extract_json_block


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, text: str = '') -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self.payload


def test_complete_sends_auth_and_response_format(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured['url'] = url
        captured.update(kwargs)
        return FakeResponse({'choices': [{'message': {'content': 'done'}}]})

    monkeypatch.setattr('agentdebug.runtime.llm.httpx.post', fake_post)
    client = OpenAICompatClient(
        base_url='https://example.invalid/v1/',
        api_key='secret',
        model='test-model',
    )

    result = client.complete(
        [{'role': 'user', 'content': 'hello'}],
        response_format={'type': 'json_object'},
        max_tokens=123,
    )

    assert result.text == 'done'
    assert captured['url'] == 'https://example.invalid/v1/chat/completions'
    assert captured['headers']['Authorization'] == 'Bearer secret'
    assert captured['json']['max_tokens'] == 123
    assert captured['json']['response_format'] == {'type': 'json_object'}


def test_complete_retries_with_reasoning_token_parameter(monkeypatch) -> None:
    bodies = []

    def fake_post(url, **kwargs):
        bodies.append(dict(kwargs['json']))
        if len(bodies) == 1:
            return FakeResponse(
                {},
                status_code=400,
                text='use max_completion_tokens instead',
            )
        return FakeResponse({'choices': [{'message': {'content': 'ok'}}]})

    monkeypatch.setattr('agentdebug.runtime.llm.httpx.post', fake_post)
    client = OpenAICompatClient(
        base_url='https://example.invalid/v1',
        api_key='secret',
        model='reasoning-model',
    )

    assert client.complete([{'role': 'user', 'content': 'hello'}]).text == 'ok'
    assert 'max_tokens' in bodies[0]
    assert 'max_completion_tokens' in bodies[1]


def test_chat_sends_tools_and_returns_raw_choice(monkeypatch) -> None:
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs['json'])
        return FakeResponse(
            {
                'choices': [
                    {
                        'finish_reason': 'tool_calls',
                        'message': {'tool_calls': [{'id': 'call-1'}]},
                    }
                ]
            }
        )

    monkeypatch.setattr('agentdebug.runtime.llm.httpx.post', fake_post)
    client = OpenAICompatClient(
        base_url='https://example.invalid/v1',
        api_key='secret',
        model='tool-model',
    )

    choice = client.chat(
        [{'role': 'user', 'content': 'search'}],
        tools=[{'type': 'function'}],
        tool_choice='auto',
        seed=7,
    )

    assert choice['finish_reason'] == 'tool_calls'
    assert captured['tools'] == [{'type': 'function'}]
    assert captured['tool_choice'] == 'auto'
    assert captured['seed'] == 7


def test_embed_short_circuits_and_normalizes_vectors(monkeypatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs['json']))
        return FakeResponse(
            {'data': [{'embedding': [1, 2.5]}, {'embedding': 'invalid'}]}
        )

    monkeypatch.setattr('agentdebug.runtime.llm.httpx.post', fake_post)
    client = OpenAICompatClient(
        base_url='https://example.invalid/v1',
        api_key='secret',
        model='chat-model',
        embedding_model='embed-model',
    )

    assert client.embed([]) == []
    assert client.embed(['hello']) == [[1.0, 2.5]]
    assert calls[0][0].endswith('/embeddings')
    assert calls[0][1] == {'model': 'embed-model', 'input': ['hello']}


def test_from_env_and_json_extraction(monkeypatch) -> None:
    monkeypatch.setenv('CUSTOM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('CUSTOM_API_KEY', 'secret')
    monkeypatch.setenv('CUSTOM_MODEL', 'configured-model')

    client = OpenAICompatClient.from_env(env_prefix='CUSTOM')

    assert client.model == 'configured-model'
    assert extract_json_block('```json\n{"ok": true}\n```') == {'ok': True}
    assert extract_json_block('prefix {"ok": true} suffix') == {'ok': True}
    assert extract_json_block('not json') is None
