from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agentdebug.inspect.ui import routes
from agentdebug.inspect.ui.branch_store import (
    _append_case_record,
    _append_debug_branch_record,
    _read_case_records,
    _read_debug_branch_records,
)
from agentdebug.rerun import HttpLiveExecutor, RerunResult
from agentdebug.rerun.executors.process_live import ProcessLiveExecutor
from agentdebug.runtime import SQLiteTraceStore
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    EventType,
)


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


def test_trace_prefers_and_switches_stored_reports(
    tmp_path,
    monkeypatch,
    failed_trajectory: AgentTrajectory,
) -> None:
    monkeypatch.chdir(tmp_path)
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(failed_trajectory)
    now = datetime.now(timezone.utc)
    older = DiagnosticReport(
        report_id='report_older',
        trace_id=failed_trajectory.trace_id,
        generated_at=now - timedelta(minutes=1),
        summary='Older LLM report.',
        metadata={'analyzer': 'LLMJudgeAnalyzer'},
    )
    latest = DiagnosticReport(
        report_id='report_latest',
        trace_id=failed_trajectory.trace_id,
        generated_at=now,
        summary='Latest DeepDebug report.',
        metadata={'analyzer': 'DeepDebugAnalyzer'},
    )
    store.save_report(older)
    store.save_report(latest)
    client = TestClient(routes.build_app(store))

    selected = client.get(f'/api/v1/traces/{failed_trajectory.trace_id}')
    assert selected.status_code == 200
    assert selected.json()['report']['report_id'] == latest.report_id
    assert selected.json()['report_source'] == 'stored'
    assert len(selected.json()['reports']) == 2

    switched = client.get(
        f'/api/v1/traces/{failed_trajectory.trace_id}',
        params={'report_id': older.report_id},
    )
    assert switched.status_code == 200
    assert switched.json()['report']['summary'] == older.summary
    assert client.get(
        f'/api/v1/traces/{failed_trajectory.trace_id}',
        params={'report_id': 'missing'},
    ).status_code == 404

    saved_case = client.post(
        '/api/v1/cases',
        json={
            'trace_id': failed_trajectory.trace_id,
            'report_id': older.report_id,
            'title': 'Older diagnosis case',
        },
    )
    assert saved_case.status_code == 200
    assert saved_case.json()['case']['report_id'] == older.report_id
    assert saved_case.json()['case']['summary'] == older.summary


def test_ui_status_reports_capabilities_without_secrets(
    ui_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv('AGENTDEBUG_RUNNER_URL', 'https://runner.internal')
    monkeypatch.setenv('AGENTDEBUG_RUNNER_TOKEN_ENV', 'RUNNER_SECRET')
    monkeypatch.setenv('RUNNER_SECRET', 'secret-value')

    payload = ui_client.get('/api/v1/status').json()

    assert payload['rerun']['configured'] is True
    assert payload['rerun']['transport'] == 'http'
    assert 'runner.internal' not in json.dumps(payload)
    assert 'secret-value' not in json.dumps(payload)


def test_ui_responses_disable_caching_and_add_security_headers(
    ui_client: TestClient,
) -> None:
    for path in ('/', '/api/v1/traces/trace_failed'):
        response = ui_client.get(path)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['x-frame-options'] == 'SAMEORIGIN'
        assert "object-src 'none'" in response.headers['content-security-policy']


def test_gui_subpage_is_not_exposed(ui_client: TestClient) -> None:
    assert ui_client.get('/gui').status_code == 404
    assert 'GUI 轨迹' not in ui_client.get('/').text


def test_ui_uses_text_only_tooltips_and_supported_exports(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/').text
    assert "tooltip.textContent = tooltipText(" in html
    assert "tooltip.innerHTML = el.getAttribute('data-tooltip')" not in html
    assert 'Export format: json, csv, pdf' in html
    assert 'debug-bundle.zip.txt' not in html


def test_timeline_playhead_uses_selected_clip_geometry(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/').text
    assert 'positionTimelinePlayhead(CURRENT_EXPANDED_EVENT_ID);' in html
    assert "child.dataset.eventId === eventId" in html
    assert "clipRect.left - stripRect.left + clipRect.width / 2" in html
    assert "playheadWidth / 2" in html
    assert "10 + activeIndex * (clipWidth + 4)" not in html


def test_unknown_ui_resources_return_not_found(ui_client: TestClient) -> None:
    assert ui_client.get('/trace/missing').status_code == 404
    assert ui_client.get('/trace/missing/event/evt').status_code == 404
    assert ui_client.get('/api/v1/traces/missing/debug-branches').status_code == 404
    assert ui_client.patch(
        '/api/v1/traces/missing/debug-sessions/session',
        json={'status': 'evaluated'},
    ).status_code == 404


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


def test_debug_session_delete_is_persisted(ui_client: TestClient) -> None:
    _append_debug_branch_record(
        {
            'branch_id': 'branch_delete',
            'session_id': 'branch_delete',
            'trace_id': 'trace_failed',
            'generated_events': [],
        }
    )

    response = ui_client.delete(
        '/api/v1/traces/trace_failed/debug-sessions/branch_delete'
    )

    assert response.status_code == 200
    assert ui_client.get(
        '/api/v1/traces/trace_failed/debug-sessions'
    ).json()['count'] == 0
    assert response.request.url.path.endswith('/branch_delete')


def test_ui_jsonl_stores_do_not_drop_concurrent_appends(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: _append_case_record(
                    {'case_id': f'case_{index}', 'trace_id': 'trace'}
                ),
                range(40),
            )
        )
        list(
            pool.map(
                lambda index: _append_debug_branch_record(
                    {'branch_id': f'branch_{index}', 'trace_id': 'trace'}
                ),
                range(40),
            )
        )

    assert len(_read_case_records()) == 40
    assert len(_read_debug_branch_records()) == 40


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
