from __future__ import annotations

import json

import pytest

from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore
from agentdebug.cli import main
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType, model_to_json
from agentdebug.workbench.models import RunRequest
from agentdebug.workbench.profiles import resolve_pipeline
from agentdebug.workbench.registry import RunRegistry
from agentdebug.workbench.service import execute_run, plan_run


def _trajectory_file(tmp_path, *, framework='test'):
    trajectory = AgentTrajectory(
        trace_id='stable-trace',
        framework=framework,
        events=[AgentEvent(trace_id='stable-trace', event_id='failed-step', event_type=EventType.ERROR, error='tool failed')],
    )
    path = tmp_path / 'trajectory.json'
    path.write_text(model_to_json(trajectory), encoding='utf-8')
    return path


def _agenterrorbench_collection(tmp_path):
    rows = []
    for trajectory_id, failure_step in (('record-one', 1), ('record-two', 2)):
        rows.append(json.dumps({
            'trajectory_id': trajectory_id,
            'task_type': 'webshop',
            'llm_model': 'test-model',
            'critical_failure_step': failure_step,
            'critical_failure_module': 'plan',
            'full_trajectory': json.dumps({
                'messages': [
                    {'role': 'user', 'content': 'Find the requested product.'},
                    {'role': 'assistant', 'content': 'Stopped before acting.'},
                ],
            }),
        }))
    path = tmp_path / 'agenterrorbench.jsonl'
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return path


def test_profiles_expose_sources_and_explicit_llm_overrides() -> None:
    pipeline = resolve_pipeline('standard', recovery_override='none')
    assert pipeline.diagnoser.source == 'profile'
    assert pipeline.recovery.source == 'override'
    assert pipeline.llm_required is False
    explicit = resolve_pipeline('standard', diagnoser_override='judge')
    assert explicit.diagnoser.source == 'override'
    assert explicit.llm_required is True
    with pytest.raises(ValueError, match='unknown diagnoser'):
        resolve_pipeline('deep', diagnoser_override='invented')
    with pytest.raises(ValueError, match='compatible only'):
        resolve_pipeline('deep', recovery_override='reflexion')


def test_plan_persists_only_a_planned_manifest(tmp_path) -> None:
    result = plan_run(RunRequest(input_reference='not-read.json', run_root=str(tmp_path)))
    assert result.status == 'planned'
    assert result.report_id is None
    run = RunRegistry(str(tmp_path)).load_run(result.run_id)
    assert run.status == 'planned'
    assert run.artifacts.trace_id is None
    assert run.artifacts.report_id is None


@pytest.mark.parametrize('store_type,suffix', [('sqlite', 'sqlite'), ('jsonl', 'jsonl')])
def test_execute_run_persists_one_consistent_identity(tmp_path, store_type, suffix) -> None:
    source = _trajectory_file(tmp_path)
    store_path = tmp_path / f'traces.{suffix}'
    result = execute_run(RunRequest(
        input_reference=str(source), profile='quick', store_type=store_type,
        store_path=str(store_path), run_root=str(tmp_path / 'state'),
    ))
    assert result.status == 'completed'
    run = RunRegistry(str(tmp_path / 'state')).load_run(result.run_id)
    store = SQLiteTraceStore(str(store_path)) if store_type == 'sqlite' else JsonlTraceStore(str(store_path))
    assert run.artifacts.trace_id == result.trace_id == 'stable-trace'
    assert run.artifacts.report_id == result.report_id
    assert run.artifacts.trajectory_snapshot_path is None
    assert result.trajectory_snapshot_path is None
    assert run.result is not None
    assert run.result['report_id'] == result.report_id
    assert store.load_trajectory(result.trace_id) is not None
    report = store.load_report(result.trace_id, result.report_id)
    assert report is not None
    assert report.metadata['debug_run_id'] == result.run_id


def test_run_does_not_duplicate_trajectory_snapshot(tmp_path) -> None:
    store_path = tmp_path / 'capture.sqlite'
    store = SQLiteTraceStore(str(store_path))
    original = AgentTrajectory(
        trace_id='capture-current',
        events=[AgentEvent(trace_id='capture-current', event_id='before-debug')],
    )
    store.save_trajectory(original)

    result = execute_run(
        RunRequest(
            input_reference='capture-current',
            profile='quick',
            store_path=str(store_path),
            run_root=str(tmp_path / 'state'),
        )
    )
    assert result.trajectory_snapshot_path is None
    assert not list((tmp_path / 'state' / 'runs').glob('*.trajectory.json'))


def test_captured_run_is_grouped_under_its_host_session(tmp_path) -> None:
    trajectory = AgentTrajectory(
        trace_id='captured-trace',
        metadata={
            'capture_host': 'claude_code',
            'capture_host_session_id': 'session-1',
        },
        events=[AgentEvent(trace_id='captured-trace', event_id='event-1')],
    )
    result = execute_run(
        RunRequest(
            input_reference='captured-trace',
            profile='quick',
            store_path=str(tmp_path / 'store.sqlite'),
            run_root=str(tmp_path / '.agentdebug'),
        ),
        trajectory=trajectory,
    )

    run_path = (
        tmp_path / '.agentdebug' / 'sessions' / 'claude_code' / 'session-1'
        / 'runs' / f'{result.run_id}.json'
    )
    payload = json.loads(run_path.read_text(encoding='utf-8'))
    assert payload['artifacts']['trace_id'] == 'captured-trace'
    assert payload['result']['report_id'] == result.report_id
    assert not list(run_path.parent.glob('*.trajectory.json'))


def test_successful_diagnosis_survives_ui_failure(tmp_path, monkeypatch) -> None:
    import agentdebug.inspect.ui.manager as manager

    class Failed:
        status = 'failed'
        error = 'startup failed'

    monkeypatch.setattr(manager, 'ensure_ui', lambda *args, **kwargs: Failed())
    result = execute_run(RunRequest(
        input_reference=str(_trajectory_file(tmp_path)), profile='quick', ui=True,
        store_path=str(tmp_path / 'store.sqlite'), run_root=str(tmp_path / 'state'),
    ))
    assert result.status == 'completed'
    assert result.report_id
    assert result.warnings[0].code == 'ui_unavailable'


def test_run_cli_json_is_one_result_object(tmp_path, capsys) -> None:
    result = main([
        'run', str(_trajectory_file(tmp_path)), '--profile', 'quick', '--json',
        '--run-root', str(tmp_path / 'state'), '--store-jsonl', str(tmp_path / 'store.jsonl'),
    ])
    captured = capsys.readouterr()
    payload = __import__('json').loads(captured.out)
    assert result == 0
    assert payload['run_id'].startswith('dbg_')
    assert payload['trace_id'] == 'stable-trace'
    assert payload['report_id'].startswith('report_')
    assert captured.out.count('\n') == 1


def test_run_cli_selects_one_trajectory_from_agenterrorbench_collection(
    tmp_path, capsys,
) -> None:
    source = _agenterrorbench_collection(tmp_path)

    result = main([
        'run', str(source), '--trajectory-id', 'record-two',
        '--profile', 'quick', '--json', '--run-root', str(tmp_path / 'state'),
        '--store-sqlite', str(tmp_path / 'store.sqlite'),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload['status'] == 'completed'
    assert payload['trace_id'] == 'aeb_record_two'
    run = RunRegistry(str(tmp_path / 'state')).load_run(payload['run_id'])
    assert run.input.trajectory_id == 'record-two'


def test_run_cli_batches_independent_agenterrorbench_records(tmp_path, capsys) -> None:
    source = _agenterrorbench_collection(tmp_path)

    exit_code = main([
        'run', str(source), '--batch', '--profile', 'quick', '--json',
        '--run-root', str(tmp_path / 'state'),
        '--store-sqlite', str(tmp_path / 'store.sqlite'),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload['status'] == 'completed'
    assert payload['total'] == payload['succeeded'] == 2
    assert payload['failed'] == 0
    assert [item['record_id'] for item in payload['items']] == [
        'agenterrorbench__line_000001', 'agenterrorbench__line_000002',
    ]
    assert {item['result']['trace_id'] for item in payload['items']} == {
        'aeb_record_one', 'aeb_record_two',
    }
    assert all(item['result']['report_id'] for item in payload['items'])
    assert len({item['result']['run_id'] for item in payload['items']}) == 2


def test_run_cli_batch_isolates_invalid_jsonl_rows(tmp_path, capsys) -> None:
    source = tmp_path / 'records.jsonl'
    source.write_text(
        json.dumps({'messages': [{'role': 'user', 'content': 'one'}]})
        + '\n{invalid json\n',
        encoding='utf-8',
    )

    exit_code = main([
        'run', str(source), '--batch', '--profile', 'quick', '--json',
        '--run-root', str(tmp_path / 'state'),
        '--store-sqlite', str(tmp_path / 'store.sqlite'),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert payload['status'] == 'partial'
    assert payload['succeeded'] == payload['failed'] == 1
    assert payload['items'][0]['result']['status'] == 'completed'
    assert payload['items'][1]['result'] is None
    assert payload['items'][1]['errors'][0]['code'] == 'invalid_input'


def test_run_cli_batch_directory_recurses_json_without_privileging_jsonl_markers(
    tmp_path, capsys,
) -> None:
    collection = tmp_path / 'collection'
    nested = collection / 'nested'
    nested.mkdir(parents=True)
    trajectory = AgentTrajectory(
        trace_id='nested-json',
        events=[AgentEvent(
            trace_id='nested-json', event_id='failed-step',
            event_type=EventType.ERROR, error='tool failed',
        )],
    )
    (nested / 'trajectory.json').write_text(
        model_to_json(trajectory), encoding='utf-8',
    )
    marker_dir = collection / 'benchmark-marker'
    marker_dir.mkdir()
    (marker_dir / 'traj.jsonl').write_text('{}\n', encoding='utf-8')

    exit_code = main([
        'run', str(collection), '--batch', '--profile', 'quick', '--json',
        '--run-root', str(tmp_path / 'state'),
        '--store-sqlite', str(tmp_path / 'store.sqlite'),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload['total'] == payload['succeeded'] == 1
    assert payload['items'][0]['record_id'] == 'nested__trajectory'
    assert payload['items'][0]['result']['trace_id'] == 'nested-json'


@pytest.mark.parametrize('profile,format_name', [
    ('gui', None),
    ('standard', 'osworld'),
    ('gui', 'osworld'),
])
def test_run_cli_never_applies_gui_specific_batch_discovery(
    tmp_path, capsys, profile, format_name,
) -> None:
    collection = tmp_path / 'collection'
    collection.mkdir()
    (collection / 'traj.jsonl').write_text('{}\n', encoding='utf-8')
    args = [
        'run', str(collection), '--batch', '--profile', profile,
        '--plan', '--json', '--run-root', str(tmp_path / 'state'),
    ]
    if format_name:
        args.extend(['--format', format_name])

    exit_code = main(args)

    assert exit_code == 2
    assert 'contains no JSON files' in capsys.readouterr().err


def test_single_gui_run_still_ingests_an_osworld_directory(
    tmp_path, monkeypatch,
) -> None:
    task_dir = tmp_path / 'osworld-task'
    task_dir.mkdir()
    (task_dir / 'traj.jsonl').write_text(json.dumps({
        'step_num': 1,
        'action': {'action_type': 'tool_use', 'command': 'click(10, 20)'},
        'reasoning': 'click the target',
        'reward': 0,
        'done': False,
        'info': {},
    }) + '\n', encoding='utf-8')
    (task_dir / 'result.txt').write_text('0', encoding='utf-8')
    for key in ('AGENTDEBUG_LLM_BASE_URL', 'AGENTDEBUG_LLM_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(tmp_path / 'missing-config.json'))

    result = execute_run(RunRequest(
        input_reference=str(task_dir), profile='gui', format_override='osworld',
        store_path=str(tmp_path / 'store.sqlite'),
        run_root=str(tmp_path / 'state'),
    ))

    assert result.status == 'partial'
    assert result.trace_id is not None
    assert result.errors[0].code == 'llm_unavailable'


def test_run_requires_selection_for_multi_trajectory_collection(tmp_path) -> None:
    result = execute_run(RunRequest(
        input_reference=str(_agenterrorbench_collection(tmp_path)),
        profile='quick', store_path=str(tmp_path / 'store.sqlite'),
        run_root=str(tmp_path / 'state'),
    ))

    assert result.status == 'failed'
    assert result.errors[0].code == 'invalid_input'
    assert '--trajectory-id' in result.errors[0].message
    assert 'record-one, record-two' in result.errors[0].message


@pytest.mark.parametrize('profile,framework', [('deep', 'test'), ('gui', 'osworld')])
def test_llm_profiles_fail_clearly_without_configuration(
    tmp_path, monkeypatch, profile, framework,
) -> None:
    for key in ('AGENTDEBUG_LLM_BASE_URL', 'AGENTDEBUG_LLM_API_KEY', 'AGENTDEBUG_LLM_MODEL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(tmp_path / 'missing-config.json'))
    result = execute_run(RunRequest(
        input_reference=str(_trajectory_file(tmp_path, framework=framework)),
        profile=profile, store_path=str(tmp_path / 'store.sqlite'),
        run_root=str(tmp_path / 'state'),
    ))
    assert result.status == 'partial'
    assert result.errors[0].code == 'llm_unavailable'
    assert result.trace_id == 'stable-trace'
    assert result.report_id is None
