from __future__ import annotations

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
    assert store.load_trajectory(result.trace_id) is not None
    report = store.load_report(result.trace_id, result.report_id)
    assert report is not None
    assert report.metadata['debug_run_id'] == result.run_id


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
