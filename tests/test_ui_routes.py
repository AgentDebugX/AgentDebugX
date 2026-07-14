from __future__ import annotations

import json

import pytest

from agentdebug.inspect.ui import routes
from agentdebug.rerun import HttpLiveExecutor, RerunResult
from agentdebug.rerun.executors.process_live import ProcessLiveExecutor
from agentdebug.runtime import SQLiteTraceStore
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType


fastapi = pytest.importorskip('fastapi')
pytest.importorskip('uvicorn')
TestClient = pytest.importorskip('fastapi.testclient').TestClient


@pytest.fixture
def ui_client(tmp_path, monkeypatch, failed_trajectory: AgentTrajectory):
    monkeypatch.chdir(tmp_path)
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(failed_trajectory)
    return TestClient(routes.build_app(store))


def test_health_trace_and_taxonomy_routes(ui_client: TestClient) -> None:
    assert ui_client.get('/healthz').json() == {'status': 'ok'}
    assert ui_client.get('/api/v1/traces').json()['traces'] == ['trace_failed']

    trace_response = ui_client.get('/api/v1/traces/trace_failed')
    assert trace_response.status_code == 200
    assert trace_response.json()['trajectory']['trace_id'] == 'trace_failed'

    assert ui_client.get('/api/v1/traces/missing').status_code == 404
    assert ui_client.get('/api/v1/taxonomy').json()['modes']


def test_case_create_list_delete_flow(ui_client: TestClient) -> None:
    missing = ui_client.post('/api/v1/cases', json={})
    assert missing.status_code == 400

    created = ui_client.post(
        '/api/v1/cases',
        json={'trace_id': 'trace_failed', 'title': 'Regression case'},
    )
    assert created.status_code == 200
    case_id = created.json()['case']['case_id']

    listed = ui_client.get('/api/v1/cases').json()
    assert listed['count'] == 1
    assert listed['cases'][0]['title'] == 'Regression case'

    deleted = ui_client.delete(f'/api/v1/cases/{case_id}')
    assert deleted.status_code == 200
    assert ui_client.get('/api/v1/cases').json()['count'] == 0


def test_debug_continuation_validates_event(ui_client: TestClient) -> None:
    assert ui_client.post(
        '/api/v1/traces/trace_failed/debug-continuation',
        json={},
    ).status_code == 400
    assert ui_client.post(
        '/api/v1/traces/trace_failed/debug-continuation',
        json={'event_id': 'missing'},
    ).status_code == 404

    response = ui_client.post(
        '/api/v1/traces/trace_failed/debug-continuation',
        json={'event_id': 'evt_plan', 'note': 'retry safely'},
    )

    assert response.status_code == 200
    assert response.json()['selected_event']['event_id'] == 'evt_plan'


def test_rerun_does_not_persist_or_return_api_key(
    ui_client: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv('AGENTDEBUG_RERUN_COMMAND', 'trusted-runner')
    monkeypatch.setenv('AGENTDEBUG_UI_RERUN_POLICY', 'from_event')

    def run_live(self, request):
        trajectory = AgentTrajectory(trace_id='trace_ui_live')
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type=EventType.TOOL_CALL,
                input={'query': 'retry'},
            )
        )
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type=EventType.TOOL_RESULT,
                output={'status': 'success'},
            )
        )
        return RerunResult(
            request=request,
            trajectory=trajectory,
            metadata={
                'execution_mode': 'live_execution',
                'observed_execution': True,
                'tools_executed': True,
                'tool_execution_count': 1,
                'runner': 'test.ui.runner',
            },
        )

    monkeypatch.setattr(ProcessLiveExecutor, 'run', run_live)
    secret = 'sk-ui-secret-that-must-not-persist'

    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={
            'event_id': 'evt_plan',
            'api_url': 'https://example.invalid/v1',
            'api_key': secret,
            'model': 'fake-model',
            'prompt_text': 'Return a continuation.',
        },
    )
    rendered = response.text
    branch_db = tmp_path / '.agentdebug' / 'debug_branches.jsonl'

    assert response.status_code == 200
    assert secret not in rendered
    assert secret not in branch_db.read_text(encoding='utf-8')
    assert response.json()['branch']['generated_events']


def test_rerun_validates_required_backend_fields(ui_client: TestClient) -> None:
    base = {'event_id': 'evt_plan', 'prompt_text': 'retry'}

    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json=base,
    )
    assert response.status_code == 503
    assert 'AGENTDEBUG_RUNNER_URL' in response.json()['detail']

    assert ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={'event_id': 'evt_plan'},
    ).status_code == 400


def test_ui_rerun_uses_persistent_http_runner(
    ui_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv('AGENTDEBUG_RUNNER_URL', 'https://runner.test')
    calls = []

    def run_live(self, request):
        calls.append(request)
        trajectory = AgentTrajectory(trace_id='trace_ui_http')
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type=EventType.TOOL_CALL,
                input={'query': 'retry'},
            )
        )
        return RerunResult(
            request=request,
            trajectory=trajectory,
            metadata={
                'execution_mode': 'live_execution',
                'observed_execution': True,
                'tools_executed': True,
                'runner': 'tests.http',
            },
        )

    monkeypatch.setattr(HttpLiveExecutor, 'run', run_live)
    monkeypatch.setattr(HttpLiveExecutor, 'close', lambda self: None)
    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={
            'event_id': 'evt_plan',
            'model': 'server-runner',
            'prompt_text': 'Preserve refund policy.',
        },
    )

    assert response.status_code == 200
    assert calls
    assert calls[0].checkpoint.policy == 'from_start'
    assert response.json()['branch']['execution_mode'] == 'live_execution'
