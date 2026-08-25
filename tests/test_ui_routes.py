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
from agentdebug.runtime.llm import CompletionResult, TokenUsage
from agentdebug.runtime import SQLiteTraceStore
from agentdebug.inspect.ui.discussion_store import SQLiteDiscussionStore
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    DiagnosticReport,
    EventType,
    Modality,
)
from agentdebug.workbench.models import DebugRun, RunArtifactRefs, RunInput
from agentdebug.workbench.profiles import resolve_pipeline
from agentdebug.workbench.registry import RunRegistry


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


def test_run_route_resolves_exact_stored_report(tmp_path, failed_trajectory) -> None:
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(failed_trajectory)
    selected = DiagnosticReport(report_id='selected', trace_id=failed_trajectory.trace_id, summary='selected report')
    newer = DiagnosticReport(report_id='newer', trace_id=failed_trajectory.trace_id, summary='must not substitute')
    store.save_report(selected)
    store.save_report(newer)
    registry = RunRegistry(str(tmp_path / 'state'))
    run = DebugRun(
        status='completed', input=RunInput(reference='fixture'), requested_profile='quick',
        resolved_pipeline=resolve_pipeline('quick'),
        artifacts=RunArtifactRefs(trace_id=failed_trajectory.trace_id, report_id='selected', store_type='sqlite', store_path=str(tmp_path / 'traces.sqlite')),
    )
    registry.create_run(run)
    client = TestClient(routes.build_app(store, run_registry=registry))
    payload = client.get(f'/api/v1/runs/{run.run_id}').json()
    assert payload['artifacts']['report_id'] == 'selected'
    assert payload['artifacts_consistent'] is True
    page = client.get(f'/runs/{run.run_id}')
    assert page.status_code == 200
    assert 'selected report' in page.text
    with store._connect() as conn:
        conn.execute('DELETE FROM diagnostic_reports WHERE report_id = ?', ('selected',))
    assert client.get(f'/runs/{run.run_id}').status_code == 409


def _visual_ui_client(tmp_path):
    source_dir = tmp_path / 'osworld_trace'
    source_dir.mkdir()
    screenshot = source_dir / 'step_1.png'
    screenshot.write_bytes(b'\x89PNG\r\n\x1a\nagentdebugx')
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'\x89PNG\r\n\x1a\noutside')
    note = source_dir / 'note.txt'
    note.write_text('not an image', encoding='utf-8')

    trajectory = AgentTrajectory(
        trace_id='osworld_visual',
        framework='osworld',
        metadata={'source_format': 'osworld', 'source_dir': str(source_dir)},
        events=[
            AgentEvent(
                trace_id='osworld_visual',
                event_id='step-1',
                event_type=EventType.AGENT_STEP,
                step_index=1,
                input='click the button',
                output='pyautogui.click(12, 34)',
                metadata={'reward': 1, 'done': False, 'action_type': 'CLICK'},
                artifacts=[
                    Artifact(
                        uri=str(screenshot),
                        modality=Modality.IMAGE,
                        media_type='image/png',
                        description='step screenshot',
                    ),
                    Artifact(
                        uri=str(note),
                        modality=Modality.TEXT,
                        media_type='text/plain',
                    ),
                    Artifact(
                        uri=str(outside),
                        modality=Modality.IMAGE,
                        media_type='image/png',
                    ),
                    Artifact(
                        uri=str(source_dir / 'missing.png'),
                        modality=Modality.IMAGE,
                        media_type='image/png',
                    ),
                ],
            ),
        ],
    )
    store = SQLiteTraceStore(str(tmp_path / 'visual.sqlite'))
    store.save_trajectory(trajectory)
    return TestClient(routes.build_app(store)), source_dir, outside


def test_visual_capability_and_image_artifact_route(tmp_path) -> None:
    client, _source_dir, _outside = _visual_ui_client(tmp_path)

    response = client.get('/api/v1/traces/osworld_visual')
    assert response.status_code == 200
    capability = response.json()['visual_capability']
    assert capability['enabled'] is True
    assert capability['default_view'] == 'visual'
    assert capability['is_cua'] is True
    assert capability['media_count'] == 1
    assert capability['events']['step-1'][0]['url'].endswith(
        '/events/step-1/artifacts/0'
    )

    image = client.get(capability['events']['step-1'][0]['url'])
    assert image.status_code == 200
    assert image.headers['content-type'].startswith('image/png')
    assert image.content.startswith(b'\x89PNG')


def test_visual_artifact_route_rejects_invalid_or_unsafe_media(tmp_path) -> None:
    client, source_dir, outside = _visual_ui_client(tmp_path)
    base = '/api/v1/traces/osworld_visual/events/step-1/artifacts'

    assert client.get('/api/v1/traces/missing/events/step-1/artifacts/0').status_code == 404
    assert client.get('/api/v1/traces/osworld_visual/events/missing/artifacts/0').status_code == 404
    assert client.get(f'{base}/99').status_code == 404
    assert client.get(f'{base}/1').status_code == 403
    assert client.get(f'{base}/2').status_code == 403
    assert client.get(f'{base}/3').status_code == 404

    symlink = source_dir / 'escaped.png'
    try:
        symlink.symlink_to(outside)
    except OSError:
        return

    store = SQLiteTraceStore(str(tmp_path / 'symlink.sqlite'))
    trajectory = AgentTrajectory(
        trace_id='osworld_symlink',
        framework='osworld',
        metadata={'source_format': 'osworld', 'source_dir': str(source_dir)},
        events=[
            AgentEvent(
                trace_id='osworld_symlink',
                event_id='step-1',
                event_type=EventType.AGENT_STEP,
                artifacts=[
                    Artifact(
                        uri=str(symlink),
                        modality=Modality.IMAGE,
                        media_type='image/png',
                    )
                ],
            )
        ],
    )
    store.save_trajectory(trajectory)
    symlink_client = TestClient(routes.build_app(store))
    assert symlink_client.get(
        '/api/v1/traces/osworld_symlink/events/step-1/artifacts/0'
    ).status_code == 403


def test_visual_view_html_contract_and_bootstrap(tmp_path) -> None:
    client, _source_dir, _outside = _visual_ui_client(tmp_path)

    page = client.get('/trace/osworld_visual')
    assert page.status_code == 200
    assert '"visual_capability":' in page.text
    assert 'data-trace-view="trace"' in page.text
    assert 'data-trace-view="visual"' in page.text
    assert 'sessionStorage.getItem(TRACE_VIEW_MODE_PREFIX + traceId)' in page.text
    assert "capability.default_view === 'visual'" in page.text
    assert 'function parseClickCoordinates(code)' in page.text
    assert 'image.naturalWidth' in page.text
    assert 'Recorded click position' in page.text
    assert "renderVisualPane('Before action'" in page.text
    assert "renderVisualPane('After action'" in page.text
    assert "renderVisualPane('Selected screenshot'" in page.text
    assert 'data-gallery-select' in page.text
    assert 'data-visual-layout="single"' in page.text
    assert 'data-visual-layout="compare"' in page.text
    assert 'sessionStorage.setItem(' in page.text
    assert 'VISUAL_LAYOUT_PREFIX + traceId' in page.text
    assert "return 'single';" in page.text
    assert 'Discuss with Debugger' in page.text
    assert 'discussion-drawer' in page.text
    assert 'data-discussion-event' in page.text
    assert 'Report revision draft' in page.text
    assert 'root cause' in page.text


def test_discussion_api_persists_generic_trace_session_and_draft(
    tmp_path,
    failed_trajectory: AgentTrajectory,
) -> None:
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(failed_trajectory)
    discussion_path = tmp_path / 'discussions.sqlite'
    discussion_store = SQLiteDiscussionStore(str(discussion_path))
    cited_event_id = failed_trajectory.events[0].event_id

    def fake_llm(messages, tools):
        assert messages[-1]['content'] == 'What is the root cause?'
        assert {tool['function']['name'] for tool in tools} == {
            'get_event_details',
            'get_event_range',
            'get_report_details',
        }
        return CompletionResult(
            text=json.dumps({
                'content': 'The first event is relevant.',
                'citations': [cited_event_id],
                'report_revision': {
                    'changes': {
                        'summary': 'Draft summary only.',
                        'root_cause_event_id': cited_event_id,
                    },
                    'citations': [cited_event_id],
                },
            }),
            raw={'provider_secret': 'must-not-persist'},
            usage=TokenUsage(prompt_tokens=12, completion_tokens=5, calls=1),
        )

    client = TestClient(routes.build_app(
        store,
        discussion_store=discussion_store,
        discussion_llm_factory=lambda _payload: fake_llm,
    ))
    created = client.post(
        '/api/v1/traces/trace_failed/discussions',
        json={'model': 'fake-model'},
    )
    assert created.status_code == 200
    session = created.json()['session']
    assert session['version'] == 0
    session_id = session['session_id']

    sent = client.post(
        f'/api/v1/traces/trace_failed/discussions/{session_id}/messages',
        json={
            'message': 'What is the root cause?',
            'expected_version': 0,
            'client_message_id': 'client-message-1',
            'api_key': 'sk-never-persist',
        },
    )
    assert sent.status_code == 200
    payload = sent.json()
    assert payload['session']['version'] == 2
    assert [message['role'] for message in payload['messages']] == [
        'user',
        'assistant',
    ]
    assistant = payload['messages'][1]
    assert assistant['citations'][0]['event_id'] == cited_event_id
    assert assistant['proposal']['changes']['summary'] == 'Draft summary only.'

    retried = client.post(
        f'/api/v1/traces/trace_failed/discussions/{session_id}/messages',
        json={
            'message': 'What is the root cause?',
            'expected_version': 0,
            'client_message_id': 'client-message-1',
        },
    )
    assert retried.status_code == 200
    assert len(retried.json()['messages']) == 2
    assert client.post(
        f'/api/v1/traces/trace_failed/discussions/{session_id}/messages',
        json={
            'message': 'stale',
            'expected_version': 0,
            'client_message_id': 'client-message-2',
        },
    ).status_code == 409

    listed = client.get('/api/v1/traces/trace_failed/discussions').json()
    assert [item['session_id'] for item in listed['sessions']] == [session_id]
    raw_db = discussion_path.read_bytes()
    assert b'sk-never-persist' not in raw_db
    assert b'must-not-persist' not in raw_db
    assert client.delete(
        f'/api/v1/traces/trace_failed/discussions/{session_id}'
    ).status_code == 200


def test_discussion_api_sanitizes_provider_failures(
    tmp_path,
    failed_trajectory: AgentTrajectory,
) -> None:
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(failed_trajectory)

    def failing_llm(_messages, _tools):
        raise RuntimeError('provider leaked sk-secret-value')

    client = TestClient(routes.build_app(
        store,
        discussion_store=SQLiteDiscussionStore(str(tmp_path / 'discuss.sqlite')),
        discussion_llm_factory=lambda _payload: failing_llm,
    ))
    created = client.post(
        '/api/v1/traces/trace_failed/discussions',
        json={'model': 'fake-model'},
    ).json()['session']

    response = client.post(
        f'/api/v1/traces/trace_failed/discussions/{created["session_id"]}/messages',
        json={
            'message': 'Explain',
            'expected_version': 0,
            'client_message_id': 'failure-1',
        },
    )
    assert response.status_code == 502
    assert response.json()['detail'] == (
        'The discussion model could not complete the request.'
    )
    assert 'secret' not in response.text


def test_upload_schema_and_native_trace_import(ui_client: TestClient) -> None:
    schema = ui_client.get('/api/v1/schema')
    assert schema.status_code == 200
    assert schema.json()['format'] == 'AgentTrajectory'

    uploaded = ui_client.post(
        '/api/v1/traces/upload',
        json={
            'allow_llm': False,
            'content': json.dumps({
                'trace_id': 'trace_uploaded',
                'goal': 'Inspect an uploaded run',
                'events': [{
                    'trace_id': 'trace_uploaded',
                    'event_id': 'upload_evt_1',
                    'agent_name': 'agent',
                    'event_type': 'run.start',
                    'step_index': 0,
                }],
            }),
        },
    )
    assert uploaded.status_code == 200
    assert uploaded.json()['imported'] == ['trace_uploaded']
    assert uploaded.json()['converters']['trace_uploaded'] == 'native'
    assert ui_client.get('/api/v1/traces/trace_uploaded').status_code == 200


def test_upload_converts_message_log_without_llm(ui_client: TestClient) -> None:
    uploaded = ui_client.post(
        '/api/v1/traces/upload',
        json={
            'allow_llm': False,
            'content': json.dumps([
                {'role': 'user', 'content': 'Find a hotel'},
                {'role': 'assistant', 'content': 'I will search.'},
            ]),
        },
    )
    assert uploaded.status_code == 200
    trace_id = uploaded.json()['imported'][0]
    assert uploaded.json()['converters'][trace_id] == 'adapter'
    assert len(ui_client.get(f'/api/v1/traces/{trace_id}').json()['trajectory']['events']) == 2


def test_heuristic_diagnose_pipeline_stores_report(ui_client: TestClient) -> None:
    response = ui_client.post(
        '/api/v1/traces/trace_failed/diagnose',
        json={
            'mode': 'heuristic',
            'attributor': 'heuristic',
            'recovery': 'none',
            'rule_pack': 'auto',
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['pipeline']['mode'] == 'heuristic'
    assert payload['stored'] is True
    report_id = payload['report']['report_id']
    selected = ui_client.get('/api/v1/traces/trace_failed', params={'report_id': report_id})
    assert selected.status_code == 200
    assert selected.json()['report_source'] == 'stored'


def test_diagnose_options_follow_cli_registry(ui_client: TestClient) -> None:
    response = ui_client.get('/api/v1/diagnose/options')
    assert response.status_code == 200
    payload = response.json()
    assert payload['modes'] == ['heuristic', 'judge', 'deep', 'gui-rca']
    assert payload['attributors'] == [
        'none',
        'heuristic',
        'all_at_once',
        'step_by_step',
        'binary_search',
        'counterfactual',
    ]
    assert payload['recoveries'] == [
        'none',
        'deepdebug',
        'reflexion',
        'critic',
        'self_refine',
        'auto_manual',
        'saga_rollback',
    ]
    assert payload['rule_packs'] == ['auto', 'core', 'agenterrorbench', 'gui', 'all']
    assert isinstance(payload['llm_configured'], bool)
    assert isinstance(payload['llm_model'], str)


def test_deepdebug_diagnose_rejects_external_pipeline_stages(
    ui_client: TestClient,
) -> None:
    attribution = ui_client.post(
        '/api/v1/traces/trace_failed/diagnose',
        json={'mode': 'deep', 'attributor': 'heuristic', 'recovery': 'none'},
    )
    assert attribution.status_code == 400
    assert 'owns attribution' in attribution.json()['detail']

    recovery = ui_client.post(
        '/api/v1/traces/trace_failed/diagnose',
        json={'mode': 'deep', 'attributor': 'none', 'recovery': 'self_refine'},
    )
    assert recovery.status_code == 400
    assert 'must be none or deepdebug' in recovery.json()['detail']


def test_mcp_rerun_does_not_require_classic_runner(
    ui_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.delenv('AGENTDEBUG_RUNNER_URL', raising=False)
    monkeypatch.delenv('AGENTDEBUG_RERUN_COMMAND', raising=False)

    def fake_mcp_rerun(**kwargs):
        trace_id = kwargs['trajectory'].trace_id
        return {
            'generated_events': [{
                'trace_id': trace_id,
                'event_id': 'mcp_evt_1',
                'agent_name': 'tool',
                'event_type': 'tool.result',
                'step_index': 2,
                'output': 'real result',
                'metadata': {'source': 'mcp_rerun'},
            }],
            'tools_executed': True,
            'tool_call_count': 1,
            'mcp_server_host': 'localhost',
            'execution_mode': 'live_mcp',
            'transcript': [],
            'elapsed_ms': 12,
        }

    monkeypatch.setattr(
        'agentdebug.inspect.ui.mcp_rerun.run_mcp_rerun',
        fake_mcp_rerun,
    )
    event_id = ui_client.get('/api/v1/traces/trace_failed').json()['trajectory']['events'][0]['event_id']
    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={
            'event_id': event_id,
            'prompt_text': 'Recover using the available tools.',
            'base_url': 'http://llm.local/v1',
            'api_key': 'test-key',
            'model': 'test-model',
            'mcp': {
                'endpoint': 'http://localhost:8000/mcp',
                'allow_private': True,
                'allow_insecure': True,
            },
        },
    )
    assert response.status_code == 200
    branch = response.json()['branch']
    assert branch['run_type'] == 'mcp_rerun'
    assert branch['tool_call_count'] == 1
    assert 'test-key' not in json.dumps(branch)


def test_plan_only_rerun_executes_nothing(
    ui_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.delenv('AGENTDEBUG_RUNNER_URL', raising=False)
    monkeypatch.delenv('AGENTDEBUG_RERUN_COMMAND', raising=False)
    event_id = ui_client.get('/api/v1/traces/trace_failed').json()['trajectory']['events'][0]['event_id']
    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={
            'event_id': event_id,
            'rerun_mode': 'plan_only',
            'checkpoint_policy': 'from_event',
            'prompt_text': 'Retry without repeating the diagnosed failure.',
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['mode'] == 'plan_only'
    assert payload['branch']['status'] == 'planned'
    assert payload['branch']['execution_mode'] is None
    assert payload['branch']['tools_executed'] is False
    assert payload['branch']['generated_events'] == []
    assert payload['plan']['request']['checkpoint']['policy'] == 'from_event'


def test_simulated_rerun_is_labeled_and_unverified(
    ui_client: TestClient,
    monkeypatch,
) -> None:
    from agentdebug.runtime.llm import CompletionResult

    def fake_complete(self, messages, **kwargs):
        return CompletionResult(
            text=json.dumps({
                'summary': 'A hypothetical corrected outcome.',
                'success': True,
                'events': [{
                    'agent_name': 'agent',
                    'event_type': 'run.end',
                    'step_index': 1,
                    'output': 'Hypothetical completion.',
                    'error': None,
                    'metadata': {},
                }],
            }),
            raw={'choices': [{'finish_reason': 'stop'}]},
        )

    monkeypatch.setattr(
        'agentdebug.runtime.llm.OpenAICompatClient.complete',
        fake_complete,
    )
    event_id = ui_client.get('/api/v1/traces/trace_failed').json()['trajectory']['events'][0]['event_id']
    response = ui_client.post(
        '/api/v1/traces/trace_failed/rerun-from-event',
        json={
            'event_id': event_id,
            'rerun_mode': 'simulate',
            'checkpoint_policy': 'from_start',
            'prompt_text': 'Simulate a corrected attempt.',
            'base_url': 'http://llm.local/v1',
            'api_key': 'simulation-secret',
            'model': 'simulation-model',
        },
    )
    assert response.status_code == 200
    branch = response.json()['branch']
    assert branch['run_type'] == 'simulated_rerun'
    assert branch['execution_mode'] == 'simulated_rollout'
    assert branch['verified'] is False
    assert branch['tools_executed'] is False
    assert branch['artifact_type'] == 'hypothetical_trajectory'
    assert branch['generated_events'][0]['metadata']['simulated'] is True
    assert 'simulation-secret' not in json.dumps(branch)


def test_rerun_composer_exposes_three_core_modes(ui_client: TestClient) -> None:
    html = ui_client.get('/').text
    assert 'data-rerun-mode="plan_only"' in html
    assert 'data-rerun-mode="simulate"' in html
    assert 'data-rerun-mode="live"' in html
    assert 'data-live-transport="server"' in html
    assert 'data-live-transport="mcp"' in html
    assert 'data-rerun-mode="classic"' not in html
    assert 'id="rerun-checkpoint-policy"' not in html
    assert 'Checkpoint: event #' in html
    assert "checkpoint_policy: 'from_event'" in html


def test_diagnose_modal_uses_backend_options_and_deepdebug_constraints(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/trace/trace_failed').text
    assert "await api('/api/v1/diagnose/options')" in html
    assert 'id="diagnose-embedding-model"' in html
    assert "embedding_model: document.getElementById('diagnose-embedding-model')" in html
    assert "attributor.value = 'none'" in html
    assert "recovery.value = 'deepdebug'" in html
    assert "['none', 'deepdebug'].includes(option.value)" in html


def test_diagnose_result_is_applied_to_the_outer_workspace(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/trace/trace_failed').text
    assert 'async function applyDiagnoseResult(traceId, reportId)' in html
    assert "'?report_id=' + encodeURIComponent(reportId)" in html
    assert "data?.report_source !== 'stored'" in html
    assert 'updateTraceCatalogFromAnalysis(data);' in html
    assert 'renderTraceList(traceIds, traceId);' in html
    assert 'renderTrace(data.trajectory, data.report);' in html
    apply_index = html.index('await applyDiagnoseResult(traceId, payload.report?.report_id);')
    close_index = html.index("closeWorkflowModal('diagnose-pipeline-modal');", apply_index)
    assert apply_index < close_index
    assert 'Diagnose report applied to workspace' in html


def test_global_llm_settings_prefill_all_llm_workflows(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/trace/trace_failed').text
    assert 'id="llm-settings-btn"' in html
    assert "modal.id = 'llm-settings-modal'" in html
    assert "const LLM_SETTINGS_STORAGE_KEY = 'agentdebugx-llm-settings-v1'" in html
    assert "const LLM_API_KEY_SESSION_KEY = 'agentdebugx-llm-api-key-v1'" in html
    assert 'sessionStorage.setItem(LLM_API_KEY_SESSION_KEY' in html
    assert "api_key: String(persisted?.api_key" not in html
    assert "base_url: ['upload-base-url', 'diagnose-base-url', 'rerun-sim-base-url', 'rerun-mcp-base-url']" in html
    assert "api_key: ['upload-api-key', 'diagnose-api-key', 'rerun-sim-api-key', 'rerun-mcp-api-key']" in html
    assert "model: ['upload-model', 'diagnose-model', 'rerun-sim-model', 'rerun-mcp-model']" in html
    assert 'data-password-toggle="' in html
    assert "input.type = visible ? 'text' : 'password'" in html
    assert "loadLLMSettings().model || loadDebugBackendConfig().debug_model" in html


def test_local_shell_places_case_action_on_each_run_and_uses_logo(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/trace/trace_failed').text
    assert 'id="run-save-case-btn"' not in html
    assert 'class="run-save-case" type="button" data-save-case' in html
    assert "saveCaseButton.dataset.traceId = tid" in html
    assert "saveTraceCase(tid, saveCaseButton)" in html
    assert 'event.stopPropagation()' in html
    assert 'id="save-case-btn"' not in html
    assert 'id="offline-status-btn"' in html
    assert 'id="offline-popover" role="dialog"' in html
    assert 'id="runtime-status-title">Local UI</span>' in html
    assert '<img src="/assets/robot-avatar.svg" alt="" />' in html

    avatar = ui_client.get('/assets/robot-avatar.svg')
    assert avatar.status_code == 200
    assert avatar.headers['content-type'].startswith('image/svg+xml')
    assert b'<svg' in avatar.content


def test_summary_cards_size_to_their_content(ui_client: TestClient) -> None:
    html = ui_client.get('/trace/trace_failed').text
    adaptive_rule = (
        'body.trace-editor-mode .summary-primary,\n'
        '  body.trace-editor-mode .summary-observation,\n'
        '  body.trace-editor-mode .summary-plan {'
    )
    assert adaptive_rule in html
    assert 'min-height:0 !important;' in html[html.index(adaptive_rule):]
    assert 'height:auto !important;' in html[html.index(adaptive_rule):]
    assert 'align-self:start !important;' in html[html.index(adaptive_rule):]


def test_rerun_attempts_have_mode_specific_visual_contract(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/').text
    assert "runType === 'rerun_plan'" in html
    assert "runType === 'simulated_rerun'" in html
    assert "runType === 'mcp_rerun'" in html
    assert "label:'Plan'" in html
    assert "label:'Simulation'" in html
    assert "'Live · MCP'" in html
    assert 'mode-plan' in html
    assert 'mode-simulate' in html
    assert 'mode-live' in html


def test_trace_workspace_uses_left_overview_and_right_hub_drawers(
    ui_client: TestClient,
) -> None:
    html = ui_client.get('/trace/trace_failed').text
    assert 'id="overview-drawer" role="dialog"' in html
    assert 'class="workspace-drawer left"' in html
    assert 'id="hub-drawer" role="dialog"' in html
    assert 'class="workspace-drawer right"' in html
    assert 'id="workspace-drawer-scrim"' in html
    assert 'aria-controls="overview-drawer"' in html
    assert 'aria-controls="hub-drawer"' in html
    assert 'class="diagnosis-hero-head"' in html
    assert '<nav class="workspace-launchers"' not in html
    assert 'id="overview-btn"' in html
    assert '<span>Overview</span>' in html
    assert '<span>Error Hub</span>' in html
    assert 'class="top-brand-avatar"' in html
    assert '<nav class="icon-rail"' not in html
    assert 'id="rail-overview-btn"' not in html
    assert 'id="rail-trace-btn"' not in html
    assert 'id="rail-cases-btn"' not in html
    assert "openWorkspaceDrawer('overview'" in html
    assert "openWorkspaceDrawer('hub'" in html
    assert 'function closeWorkspaceDrawer' in html

    root_html = ui_client.get('/').text
    assert '"view": "trace"' in root_html
    assert '"trace_id": "trace_failed"' in root_html


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
            'checkpoint_policy': 'from_event',
            'model': 'server-runner',
            'prompt_text': 'Preserve refund policy.',
        },
    )

    assert response.status_code == 200
    assert calls
    assert calls[0].checkpoint.policy == 'from_event'
    assert calls[0].checkpoint.event_id == 'evt_plan'
    assert response.json()['branch']['execution_mode'] == 'live_execution'
