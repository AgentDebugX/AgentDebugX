from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest

from agentdebug.rerun import (
    HTTP_RUNNER_PROTOCOL_VERSION,
    HttpLiveExecutor,
    HttpRunnerCapabilities,
    RerunWorkflow,
    build_rerun_request,
    create_http_runner_app,
    load_live_runner,
)
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType, model_to_dict


def _live_result(source: AgentTrajectory, *, runner: str = 'tests.http') -> dict:
    trace_id = f'{source.trace_id}__http_live'
    trajectory = AgentTrajectory(
        trace_id=trace_id,
        task_id=source.task_id,
        goal=source.goal,
        framework='test-framework',
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.TOOL_CALL,
            input={'operation': 'lookup_refundable'},
        )
    )
    trajectory.add_event(
        AgentEvent(
            trace_id=trace_id,
            event_type=EventType.TOOL_RESULT,
            output={'refundable': True},
        )
    )
    return {
        'execution': {
            'mode': 'live_execution',
            'observed_execution': True,
            'tools_executed': True,
            'tool_execution_count': 1,
            'runner': runner,
            'framework': 'test-framework',
        },
        'trajectory': model_to_dict(trajectory),
        'metadata': {'summary': 'real tool callback completed'},
    }


def test_http_runner_service_executes_real_callback(
    failed_trajectory: AgentTrajectory,
    diagnostic_report,
) -> None:
    fastapi = pytest.importorskip('fastapi')
    TestClient = pytest.importorskip('fastapi.testclient').TestClient
    calls = []

    def runner(request, source, cancel_event):
        calls.append((request, source, cancel_event))
        return _live_result(source)

    app = create_http_runner_app(
        runner,
        HttpRunnerCapabilities(
            runner='tests.http',
            framework='test-framework',
            checkpoint_policies=('from_start',),
        ),
        bearer_token='runner-secret',
    )
    client = TestClient(app)
    headers = {'Authorization': 'Bearer runner-secret'}
    request = build_rerun_request(
        diagnostic_report,
        failed_trajectory,
        checkpoint_policy='from_start',
    )

    assert client.get('/v1/capabilities').status_code == 401
    capabilities = client.get('/v1/capabilities', headers=headers).json()
    assert capabilities['protocol_version'] == HTTP_RUNNER_PROTOCOL_VERSION
    assert capabilities['live_execution'] is True

    response = client.post(
        '/v1/reruns',
        headers=headers,
        json={
            'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
            'request': __import__('dataclasses').asdict(request),
            'source_trajectory': model_to_dict(failed_trajectory),
        },
    )
    assert response.status_code == 202
    run_id = response.json()['run_id']
    for _ in range(100):
        status = client.get(f'/v1/reruns/{run_id}', headers=headers).json()
        if status['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(0.005)

    assert status['status'] == 'succeeded'
    result = client.get(
        f'/v1/reruns/{run_id}/trajectory', headers=headers
    ).json()
    assert result['execution']['tools_executed'] is True
    assert calls[0][0].directive.text == request.directive.text
    assert calls[0][1].trace_id == failed_trajectory.trace_id


def test_http_live_executor_handshake_poll_and_result(
    failed_trajectory: AgentTrajectory,
    diagnostic_report,
) -> None:
    statuses = iter(['queued', 'running', 'succeeded'])
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer runner-secret'
        if request.url.path == '/v1/capabilities':
            return httpx.Response(
                200,
                json={
                    'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
                    'runner': 'tests.http',
                    'framework': 'test-framework',
                    'live_execution': True,
                    'checkpoint_policies': ['from_start'],
                },
            )
        if request.method == 'POST' and request.url.path == '/v1/reruns':
            payload = json.loads(request.content)
            assert payload['source_trajectory']['trace_id'] == 'trace_failed'
            return httpx.Response(202, json={'run_id': 'run_test', 'status': 'queued'})
        if request.url.path == '/v1/reruns/run_test':
            return httpx.Response(
                200, json={'run_id': 'run_test', 'status': next(statuses)}
            )
        if request.url.path == '/v1/reruns/run_test/trajectory':
            return httpx.Response(200, json=_live_result(failed_trajectory))
        raise AssertionError(f'unexpected request: {request.method} {request.url}')

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={'Authorization': 'Bearer runner-secret'},
    )
    executor = HttpLiveExecutor(
        'https://runner.test',
        failed_trajectory,
        token='runner-secret',
        poll_interval=0,
        client=client,
        sleep=lambda _: None,
    )
    result = RerunWorkflow(executor).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
        checkpoint_policy='from_start',
    )

    assert result.execution is not None
    assert result.execution.metadata['executor'] == 'http_live'
    assert result.execution.metadata['remote_run_id'] == 'run_test'
    assert result.execution.metadata['tools_executed'] is True
    assert result.execution.trajectory.metadata['remote_run_id'] == 'run_test'
    assert any(request.url.path.endswith('/trajectory') for request in requests)


def test_http_live_executor_rejects_unsupported_checkpoint(
    failed_trajectory: AgentTrajectory,
    diagnostic_report,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
                json={
                    'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
                    'live_execution': True,
                    'runner': 'tests.http',
                    'framework': 'test-framework',
                    'checkpoint_policies': ['from_start'],
                },
        )

    executor = HttpLiveExecutor(
        'https://runner.test',
        failed_trajectory,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ValueError, match='does not support checkpoint policy'):
        RerunWorkflow(executor).run(
            diagnostic_report,
            failed_trajectory,
            execute=True,
            checkpoint_policy='from_event',
            checkpoint_event_id='evt_plan',
        )


def test_http_runner_cancel_sets_cooperative_signal(
    failed_trajectory: AgentTrajectory,
    diagnostic_report,
) -> None:
    TestClient = pytest.importorskip('fastapi.testclient').TestClient
    started = threading.Event()
    observed_cancel = threading.Event()

    def runner(request, source, cancel_event):
        started.set()
        if cancel_event.wait(1):
            observed_cancel.set()
            raise RuntimeError('cancelled by caller')
        return _live_result(source)

    client = TestClient(
        create_http_runner_app(
            runner,
            HttpRunnerCapabilities(runner='tests.cancel', framework='test'),
        )
    )
    request = build_rerun_request(
        diagnostic_report, failed_trajectory, checkpoint_policy='from_start'
    )
    submission = client.post(
        '/v1/reruns',
        json={
            'protocol_version': HTTP_RUNNER_PROTOCOL_VERSION,
            'request': __import__('dataclasses').asdict(request),
            'source_trajectory': model_to_dict(failed_trajectory),
        },
    ).json()
    assert started.wait(1)
    response = client.post(f'/v1/reruns/{submission["run_id"]}/cancel')

    assert response.status_code == 200
    assert observed_cancel.wait(1)


def test_http_live_executor_over_real_uvicorn_socket(
    failed_trajectory: AgentTrajectory,
    diagnostic_report,
) -> None:
    uvicorn = pytest.importorskip('uvicorn')

    def runner(request, source, cancel_event):
        return _live_result(source, runner='tests.uvicorn')

    app = create_http_runner_app(
        runner,
        HttpRunnerCapabilities(
            runner='tests.uvicorn',
            framework='test-framework',
        ),
    )
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 0))
        except PermissionError:
            pytest.skip('environment does not permit local listening sockets')
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error')
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    executor = HttpLiveExecutor(
        f'http://127.0.0.1:{port}',
        failed_trajectory,
        poll_interval=0.01,
        timeout=5,
    )
    try:
        result = RerunWorkflow(executor).run(
            diagnostic_report,
            failed_trajectory,
            execute=True,
            checkpoint_policy='from_start',
        )
    finally:
        executor.close()
        server.should_exit = True
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert result.execution is not None
    assert result.execution.metadata['runner'] == 'tests.uvicorn'
    assert result.execution.trajectory.events[0].event_type == 'tool.call'


def test_load_live_runner_entrypoint() -> None:
    runner = load_live_runner('examples.http_agent_runner:run_agent')

    assert callable(runner)
    with pytest.raises(ValueError, match='module:function'):
        load_live_runner('invalid-entrypoint')


@pytest.mark.parametrize(
    'url',
    ['', 'runner.local', 'ftp://runner.local', 'https://user:pass@runner.local'],
)
def test_http_live_executor_rejects_unsafe_urls(
    url: str,
    failed_trajectory: AgentTrajectory,
) -> None:
    with pytest.raises(ValueError, match='absolute http'):
        HttpLiveExecutor(url, failed_trajectory)
