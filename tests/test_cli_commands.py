from __future__ import annotations

import json
import importlib
import sys
from argparse import Namespace
from types import SimpleNamespace

from agentdebug.cli import main
from agentdebug.cli import legacy
from agentdebug.rerun import HttpLiveExecutor, RerunResult
from agentdebug.runtime import CompletionResult, OpenAICompatClient, SQLiteTraceStore
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticAuditEntry,
    DiagnosticReport,
    model_to_json,
)


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


def test_batch_ingest_cli_processes_jsonl_records(tmp_path, capsys) -> None:
    source = tmp_path / 'records.jsonl'
    source.write_text(
        '\n'.join(
            [
                json.dumps({'messages': [{'role': 'user', 'content': 'one'}]}),
                json.dumps({'messages': [{'role': 'user', 'content': 'two'}]}),
            ]
        ),
        encoding='utf-8',
    )
    output = tmp_path / 'batch'

    result = main(['batch', 'ingest', str(source), '--out-dir', str(output)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['total'] == 2
    assert payload['succeeded'] == 2
    assert len(list(output.glob('*.trajectory.json'))) == 2


def test_batch_diagnose_cli_isolates_bad_json(tmp_path, capsys) -> None:
    source = tmp_path / 'records'
    source.mkdir()
    (source / 'valid.json').write_text(
        json.dumps({'messages': [{'role': 'user', 'content': 'ok'}]}),
        encoding='utf-8',
    )
    (source / 'invalid.json').write_text('{invalid}', encoding='utf-8')
    output = tmp_path / 'diagnosed'

    result = main(
        [
            'batch',
            'diagnose',
            str(source),
            '--mode',
            'heuristic',
            '--attributor',
            'none',
            '--recovery',
            'none',
            '--out-dir',
            str(output),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 3
    assert payload['succeeded'] == 1
    assert payload['failed'] == 1
    assert (output / 'reports' / 'valid.report.json').exists()
    assert (output / 'batch-summary.json').exists()


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


def test_cli_recovery_targets_primary_attribution(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    from agentdebug.diagnose.attribute import AttributionResult, Blame

    diagnostic_report.findings[0].event_id = 'evt_tool'
    diagnostic_report.findings[0].agent_name = 'browser'
    diagnostic_report.findings[0].step_index = 2
    source = tmp_path / 'trace.json'
    source.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    monkeypatch.setattr(
        'agentdebug.cli.legacy._run_diagnose_mode',
        lambda args, trajectory, diagnose_mode, llm: diagnostic_report,
    )
    monkeypatch.setattr(
        'agentdebug.cli.legacy._run_attributor',
        lambda attributor_mode, trajectory, report, llm: AttributionResult(
            method='test',
            hypotheses=[
                Blame(
                    span_id='evt_plan',
                    step_index=1,
                    agent_name='planner',
                    confidence=0.9,
                    rationale='The plan caused the later tool failure.',
                    evidence=['Search for the cheapest flight.'],
                )
            ],
        ),
    )

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
    assert payload['root_cause_event_id'] == 'evt_plan'
    assert payload['metadata']['recovery_target']['source'] == 'primary_attribution'
    assert payload['recovery']['primary']['target_event_id'] == 'evt_plan'


def test_deepdebug_mode_automatically_builds_retry_directive(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    source = tmp_path / 'trace.json'
    source.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    diagnostic_report.audit = [
        DiagnosticAuditEntry(
            stage='cross_examine',
            request_summary='compare candidates',
            response_summary='selected evt_plan',
            duration_ms=17,
            payload={'verdict': 'agreement', 'event_id': 'evt_plan'},
        )
    ]
    monkeypatch.setattr(
        'agentdebug.cli.legacy._run_diagnose_mode',
        lambda args, trajectory, diagnose_mode, llm: diagnostic_report,
    )

    result = main(
        [
            'diagnose',
            str(source),
            '--mode',
            'deepdebug',
            '--base-url',
            'https://example.invalid/v1',
            '--api-key',
            'secret',
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['recovery']['method'] == 'deepdebug'
    assert payload['recovery']['primary']['recoverer_id'] == 'deepdebug'
    assert 'Fix for the retry:' in payload['recovery']['primary']['suggestion_text']
    assert payload['audit'][0] == {
        'stage': 'cross_examine',
        'request_summary': 'compare candidates',
        'response_summary': 'selected evt_plan',
        'duration_ms': 17,
        'payload': {'verdict': 'agreement', 'event_id': 'evt_plan'},
    }


def test_deepdebug_mode_seeds_analysis_with_detect_findings(
    monkeypatch,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    captured = {}

    class Analyzer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def analyze(self, trajectory):
            return SimpleNamespace(report=diagnostic_report)

    class MemoryStore:
        def __init__(self, **kwargs):
            captured['memory_kwargs'] = kwargs

    deep_module = importlib.import_module('agentdebug.deep')
    memory_module = importlib.import_module('agentdebug.deep_memory')
    monkeypatch.setattr(deep_module, 'DeepDebugAnalyzer', Analyzer)
    monkeypatch.setattr(memory_module, 'SQLiteDeepMemoryStore', MemoryStore)

    report = legacy._run_diagnose_mode(
        Namespace(rule_pack='core', embedding_model=None),
        failed_trajectory,
        'deep',
        SimpleNamespace(model='test-model'),
    )

    prior_findings = captured['prior_findings']
    assert prior_findings
    assert any(finding.event_id == 'evt_tool' for finding in prior_findings)
    assert report.metadata['upstream_detect']['finding_count'] == len(prior_findings)
    assert report.metadata['upstream_detect']['findings'][0]['failure_mode_id']


def test_deepdebug_keeps_its_builtin_attribution(
    monkeypatch,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.attribution = {
        'method': 'deepdebug',
        'primary': {'span_id': 'evt_plan', 'step_index': 1},
    }
    monkeypatch.setattr(
        legacy,
        '_run_diagnose_mode',
        lambda args, trajectory, diagnose_mode, llm: diagnostic_report,
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError('external attributor must not override DeepDebug')

    monkeypatch.setattr(legacy, '_run_attributor', fail_if_called)

    report = legacy._run_diagnose_pipeline(
        Namespace(),
        failed_trajectory,
        diagnose_mode='deep',
        attributor_mode='all_at_once',
        recovery_mode='none',
        llm=SimpleNamespace(model='test-model'),
    )

    assert report.attribution['method'] == 'deepdebug'
    assert report.attribution['primary']['span_id'] == 'evt_plan'


def test_deepdebug_recovery_is_an_explicit_cli_choice(
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
            '--recovery',
            'deepdebug',
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['recovery']['method'] == 'deepdebug'


def test_deepdebug_explicit_none_preserves_compatibility(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    source = tmp_path / 'trace.json'
    source.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    monkeypatch.setattr(
        'agentdebug.cli.legacy._run_diagnose_mode',
        lambda args, trajectory, diagnose_mode, llm: diagnostic_report,
    )

    result = main(
        [
            'diagnose',
            str(source),
            '--mode',
            'deepdebug',
            '--recovery',
            'none',
            '--base-url',
            'https://example.invalid/v1',
            '--api-key',
            'secret',
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload.get('recovery') is None


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


def test_http_runner_config_lifecycle(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / 'config.json'
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(config_path))

    assert main(
        [
            'config',
            'set-runner',
            'research',
            '--url',
            'https://runner.example/v1/',
            '--token-env',
            'RESEARCH_RUNNER_TOKEN',
            '--poll-interval',
            '0.25',
            '--default',
        ]
    ) == 0
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['default_runner'] == 'research'
    assert config['runners']['research']['url'] == 'https://runner.example'
    assert config['runners']['research']['token_env'] == 'RESEARCH_RUNNER_TOKEN'
    assert config['runners']['research']['max_retries'] == 3
    assert 'token' not in config['runners']['research']

    capsys.readouterr()
    assert main(['config', 'list-runners']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed['default_runner'] == 'research'

    assert main(['config', 'remove-runner', 'research']) == 0
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert config['runners'] == {}
    assert config['default_runner'] is None


def test_runner_config_rejects_embedded_credentials(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(tmp_path / 'config.json'))

    result = main(
        [
            'config',
            'set-runner',
            'unsafe',
            '--url',
            'https://user:password@runner.example',
        ]
    )

    assert result == 2
    assert 'absolute http' in capsys.readouterr().err


def test_config_doctor_runner_prints_capabilities(
    tmp_path, monkeypatch, capsys
) -> None:
    config_path = tmp_path / 'config.json'
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(config_path))
    monkeypatch.setenv('RUNNER_TOKEN', 'runner-secret')
    assert main(
        [
            'config',
            'set-runner',
            'local',
            '--url',
            'http://127.0.0.1:8765',
            '--token-env',
            'RUNNER_TOKEN',
        ]
    ) == 0
    capsys.readouterr()
    monkeypatch.setattr(
        HttpLiveExecutor,
        'capabilities',
        lambda self: {
            'protocol_version': '1.0',
            'live_execution': True,
            'runner': 'local',
        },
    )

    assert main(['config', 'doctor-runner', 'local']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['ok'] is True
    assert payload['capabilities']['live_execution'] is True


def test_rerun_output_never_contains_provided_api_key(
    tmp_path,
    capsys,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    secret = 'sk-never-print-this-value'

    result = main(['rerun', str(report_path), '--api-key', secret, '--plan-only'])
    rendered = capsys.readouterr().out

    assert result == 0
    assert secret not in rendered
    assert json.loads(rendered)['executed'] is False


def test_rerun_plan_exports_actor_task_jsonl(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    output_path = tmp_path / 'actor-tasks.jsonl'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
            '--plan-only',
            '--actor-task-format',
            'jsonl',
            '--out',
            str(output_path),
        ]
    )

    assert result == 0
    assert capsys.readouterr().out == ''
    record = json.loads(output_path.read_text(encoding='utf-8'))
    assert record['record_type'] == 'agentdebug.rerun.actor_task'
    assert record['status'] == 'pending'
    assert record['requires_live_environment'] is True
    assert record['checkpoint_policy'] == 'from_start'


def test_rerun_modes_are_mutually_exclusive(
    tmp_path,
    capsys,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')

    result = main(
        ['rerun', str(report_path), '--plan-only', '--simulate']
    )

    assert result == 2
    assert 'either --plan-only or --simulate' in capsys.readouterr().err


def test_actor_task_export_requires_matching_output_suffix(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
            '--plan-only',
            '--actor-task-format',
            'jsonl',
            '--out',
            str(tmp_path / 'tasks.parquet'),
        ]
    )

    assert result == 2
    assert 'requires an .jsonl --out path' in capsys.readouterr().err


def test_rerun_executes_full_model_rollout(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    calls = []

    def complete(self, messages, **kwargs):
        calls.append(messages)
        return CompletionResult(
            text=(
                '{"summary":"fixed hypothetically","success":true,"events":['
                '{"event_type":"plan","step_index":1,"output":"fixed"}]}'
            ),
            raw={},
        )

    monkeypatch.setattr(OpenAICompatClient, 'complete', complete)
    secret = 'sk-cli-rerun-secret'

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
            '--base-url',
            'https://example.invalid/v1',
            '--api-key',
            secret,
            '--model',
            'rerun-model',
            '--simulate',
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 0
    assert calls
    assert payload['executed'] is True
    assert payload['plan']['request']['checkpoint']['policy'] == 'from_start'
    assert payload['trajectory']['events'][0]['step_index'] == 1
    assert payload['status'] == 'simulated'
    assert payload['execution_mode'] == 'simulated_rollout'
    assert payload['live_execution'] is False
    assert payload['verified'] is False
    assert payload['execution']['metadata']['execution_mode'] == 'simulated_rollout'
    assert payload['execution']['metadata']['artifact_type'] == 'hypothetical_trajectory'
    assert payload['evaluation']['verified_task_outcome'] is False
    assert secret not in rendered


def test_cli_rerun_refuses_trajectory_only_execution(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
        ]
    )

    assert result == 4
    error = capsys.readouterr().err
    assert '--runner-command' in error
    assert '--simulate' in error


def test_cli_rerun_rejects_live_and_simulated_modes_together(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
            '--runner-command',
            'trusted-runner',
            '--simulate',
        ]
    )

    assert result == 2
    assert 'only one of --runner, --runner-command, or --simulate' in (
        capsys.readouterr().err
    )


def test_rerun_saves_generated_trace_to_selected_store(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    store_path = tmp_path / 'traces.sqlite'
    store = SQLiteTraceStore(str(store_path))
    store.save_trajectory(failed_trajectory)
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    monkeypatch.setattr(
        OpenAICompatClient,
        'complete',
        lambda self, messages, **kwargs: CompletionResult(
            text=(
                '{"summary":"fixed hypothetically","success":true,"events":['
                '{"event_type":"agent.step","output":"fixed"}]}'
            ),
            raw={},
        ),
    )

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            failed_trajectory.trace_id,
            '--store-sqlite',
            str(store_path),
            '--base-url',
            'https://example.invalid/v1',
            '--api-key',
            'secret',
            '--simulate',
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert store.load_trajectory(payload['stored_trace_id']) is not None


def test_cli_rerun_executes_trusted_live_runner(
    tmp_path,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    runner_path = tmp_path / 'runner.py'
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    runner_path.write_text(
        '\n'.join(
            [
                'import json, os',
                'assert os.environ["AGENTDEBUG_LIVE_BASE_URL"] == "https://live.test/v1"',
                'assert os.environ["AGENTDEBUG_LIVE_API_KEY"] == "live-secret"',
                'assert os.environ["AGENTDEBUG_LIVE_MODEL"] == "live-model"',
                'request = json.load(open(os.environ["AGENTDEBUG_RERUN_REQUEST"]))',
                'source = json.load(open(os.environ["AGENTDEBUG_RERUN_SOURCE"]))',
                'assert "refund_policy" in request["directive"]["text"]',
                'trace_id = source["trace_id"] + "__cli_live"',
                'result = {"execution": {"mode": "live_execution",',
                '  "observed_execution": True,',
                '  "tools_executed": True, "tool_execution_count": 1,',
                '  "runner": "tests.cli_runner", "framework": "test-framework"},',
                ' "trajectory": {"trace_id": trace_id, "framework": "test-framework",',
                '  "events": [{"trace_id": trace_id, "event_type": "tool.call",',
                '    "input": {"refund_policy": "refundable"}},',
                '   {"trace_id": trace_id, "event_type": "tool.result",',
                '    "output": {"status": "confirmed"}}]},',
                ' "metadata": {"summary": "live execution complete"}}',
                'json.dump(result, open(os.environ["AGENTDEBUG_RERUN_OUTPUT"], "w"))',
            ]
        ),
        encoding='utf-8',
    )

    result = main(
        [
            'rerun',
            str(report_path),
            '--trajectory',
            str(trace_path),
            '--runner-command',
            f'{sys.executable} {runner_path}',
            '--base-url',
            'https://live.test/v1',
            '--api-key',
            'live-secret',
            '--model',
            'live-model',
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 0
    assert payload['executed'] is True
    assert payload['plan']['capability']['level'] == 'live_runner'
    assert payload['execution']['metadata']['execution_mode'] == 'live_execution'
    assert payload['execution']['metadata']['tools_executed'] is True
    assert payload['trajectory']['events'][0]['event_type'] == 'tool.call'
    assert 'live-secret' not in rendered


def test_cli_rerun_uses_configured_default_http_runner(
    tmp_path,
    monkeypatch,
    capsys,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    config_path = tmp_path / 'config.json'
    report_path = tmp_path / 'report.json'
    trace_path = tmp_path / 'trace.json'
    monkeypatch.setenv('AGENTDEBUG_CONFIG', str(config_path))
    report_path.write_text(model_to_json(diagnostic_report), encoding='utf-8')
    trace_path.write_text(model_to_json(failed_trajectory), encoding='utf-8')
    assert main(
        [
            'config',
            'set-runner',
            'persistent',
            '--url',
            'https://runner.test',
        ]
    ) == 0
    capsys.readouterr()

    def run_live(self, request):
        trajectory = AgentTrajectory(trace_id='trace_http_cli')
        trajectory.add_event(
            AgentEvent(
                trace_id=trajectory.trace_id,
                event_type='tool.call',
                input={'operation': 'real'},
            )
        )
        return RerunResult(
            request=request,
            trajectory=trajectory,
            metadata={
                'execution_mode': 'live_execution',
                'observed_execution': True,
                'tools_executed': True,
                'runner': 'persistent',
            },
        )

    monkeypatch.setattr(HttpLiveExecutor, 'run', run_live)
    monkeypatch.setattr(HttpLiveExecutor, 'close', lambda self: None)
    result = main(
        ['rerun', str(report_path), '--trajectory', str(trace_path)]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload['execution_mode'] == 'live_execution'
    assert payload['live_execution'] is True
    assert payload['execution']['metadata']['runner'] == 'persistent'


def test_missing_input_returns_error_code(tmp_path, capsys) -> None:
    result = main(['ingest', str(tmp_path / 'missing.json')])

    assert result == 2
    assert 'convert failed' in capsys.readouterr().err
