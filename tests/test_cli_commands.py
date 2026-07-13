from __future__ import annotations

import json

from agentdebug.cli import main
from agentdebug.runtime import SQLiteTraceStore
from agentdebug.schema import AgentTrajectory, DiagnosticReport, model_to_json


def test_ingest_command_writes_normalized_output(tmp_path) -> None:
    source = tmp_path / 'messages.json'
    output = tmp_path / 'trace.json'
    source.write_text(
        json.dumps({'messages': [{'role': 'user', 'content': 'hello'}]}),
        encoding='utf-8',
    )

    result = main(['ingest', str(source), '--out', str(output)])
    payload = json.loads(output.read_text(encoding='utf-8'))

    assert result == 0
    assert payload['events'][0]['output'] == 'hello'


def test_convert_compatibility_alias(tmp_path) -> None:
    source = tmp_path / 'messages.json'
    source.write_text(
        json.dumps({'messages': [{'role': 'user', 'content': 'hello'}]}),
        encoding='utf-8',
    )

    assert main(['convert', str(source)]) == 0


def test_diagnose_command_supports_explicit_pipeline(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
) -> None:
    source = tmp_path / 'trace.json'
    source.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(
        [
            'diagnose',
            str(source),
            '--mode',
            'heuristic',
            '--attributor',
            'heuristic',
            '--recovery',
            'reflexion',
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['trace_id'] == failed_trajectory.trace_id
    assert payload['attribution']['method'] == 'heuristic'
    assert payload['recovery']['method'] == 'reflexion'


def test_analyze_compatibility_alias_uses_local_defaults(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
) -> None:
    source = tmp_path / 'trace.json'
    source.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(['analyze', str(source)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['metadata']['analyzer'] == 'HeuristicAnalyzer'


def test_list_and_show_commands(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
) -> None:
    path = tmp_path / 'traces.sqlite'
    store = SQLiteTraceStore(str(path))
    store.save_trajectory(failed_trajectory)

    assert main(['list', '--store-sqlite', str(path)]) == 0
    assert failed_trajectory.trace_id in capsys.readouterr().out

    assert main(['show', '--store-sqlite', str(path), failed_trajectory.trace_id]) == 0
    assert json.loads(capsys.readouterr().out)['trace_id'] == failed_trajectory.trace_id


def test_show_unknown_trace_returns_specific_code(tmp_path, capsys) -> None:
    path = tmp_path / 'traces.sqlite'

    result = main(['show', '--store-sqlite', str(path), 'missing'])

    assert result == 3
    assert 'Unknown trace_id' in capsys.readouterr().err


def test_config_masks_api_key(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / 'config.json'
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(config_path))
    secret = 'sk-test-secret-value-1234567890'

    assert main(
        [
            'config',
            'set-llm',
            '--base-url',
            'https://example.invalid/v1',
            '--api-key',
            secret,
            '--model',
            'test-model',
        ]
    ) == 0
    capsys.readouterr()
    assert main(['config', 'show']) == 0
    rendered = capsys.readouterr().out

    assert secret not in rendered
    assert 'sk-t...7890' in rendered


def test_rerun_output_never_contains_provided_api_key(
    tmp_path,
    capsys,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    secret = 'sk-never-print-this-value'

    result = main(['rerun', str(report_path), '--api-key', secret])
    rendered = capsys.readouterr().out

    assert result == 0
    assert secret not in rendered
    assert json.loads(rendered)['llm']['api_key_provided'] is True


def test_missing_input_returns_error_code(tmp_path, capsys) -> None:
    result = main(['ingest', str(tmp_path / 'missing.json')])

    assert result == 2
    assert 'convert failed' in capsys.readouterr().err
