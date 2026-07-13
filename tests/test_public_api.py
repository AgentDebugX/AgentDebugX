from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.9 compatibility
    import tomli as tomllib


def test_public_api_records_and_analyzes_trace(tmp_path: Path) -> None:
    from agentdebug import AgentDebug, EventType, JsonlTraceStore

    store = JsonlTraceStore(str(tmp_path / 'traces.jsonl'))
    debugger = AgentDebug(store=store)

    with debugger.trace(goal='exercise the public API') as trace:
        trace.record(
            EventType.TOOL_RESULT,
            agent_name='search',
            module='tool',
            error='JSON schema validation failed: missing parameter query',
        )
        report = trace.analyze()

    assert report.trace_id == trace.trajectory.trace_id
    assert report.findings
    assert store.load_trajectory(trace.trajectory.trace_id) is not None


def test_new_architecture_facades_match_existing_implementations() -> None:
    from agentdebug import DiagnosePipeline
    from agentdebug.attribution import HeuristicAttributor as LegacyAttributor
    from agentdebug.models import AgentTrajectory as LegacyTrajectory
    from agentdebug.recovery import ReflexionSuggestion as LegacyRecoverer
    from agentdebug.schema import AgentTrajectory
    from agentdebug.runtime import JsonlTraceStore
    from agentdebug.diagnose.detect import HeuristicAnalyzer
    from agentdebug.diagnose.attribute import HeuristicAttributor
    from agentdebug.diagnose.recover import ReflexionSuggestion
    from agentdebug.rerun import RerunCheckpoint, RerunDirective, RerunRequest
    from agentdebug.hub import BundleManifest
    from agentdebug.integrations import DebugSkillBundle

    assert AgentTrajectory is LegacyTrajectory
    assert HeuristicAttributor is LegacyAttributor
    assert ReflexionSuggestion is LegacyRecoverer
    assert JsonlTraceStore.__name__ == 'JsonlTraceStore'
    assert HeuristicAnalyzer.__name__ == 'HeuristicAnalyzer'
    assert BundleManifest.__name__ == 'BundleManifest'
    assert DebugSkillBundle.__name__ == 'DebugSkillBundle'

    request = RerunRequest(
        trace_id='trace_test',
        checkpoint=RerunCheckpoint(step_index=1),
        directive=RerunDirective(text='retry with the diagnosed fix'),
    )
    assert request.checkpoint.policy == 'from_root_cause'

    pipeline = DiagnosePipeline.local_default()
    assert pipeline.attributor is not None


def test_diagnose_pipeline_runs_detect_and_attribute() -> None:
    from agentdebug.schema import AgentEvent, AgentTrajectory, EventType
    from agentdebug.diagnose.pipeline import DiagnosePipeline

    trajectory = AgentTrajectory(trace_id='trace_pipeline', goal='test pipeline')
    trajectory.add_event(
        AgentEvent(
            trace_id=trajectory.trace_id,
            event_type=EventType.TOOL_RESULT,
            agent_name='search',
            step_index=1,
            error='JSON schema validation failed: missing parameter query',
        )
    )

    result = DiagnosePipeline.local_default().run(trajectory)

    assert result.report.findings
    assert result.attribution is not None
    assert result.report.attribution is not None
    assert result.report.root_cause_agent == 'search'


def test_rule_packs_are_manifest_backed_components() -> None:
    from agentdebug.diagnose.detect.rules import (
        RulePackMetadata,
        available_rule_packs,
        get_rule_pack_metadata,
        list_rule_packs,
    )
    from agentdebug.diagnose.detect.rules.core import KeywordRule
    from agentdebug.diagnose.rules.core import KeywordRule as LegacyKeywordRule

    pack_ids = available_rule_packs()
    metadata = {pack.id: pack for pack in list_rule_packs()}

    assert pack_ids == ['core', 'agenterrorbench', 'gui']
    assert isinstance(metadata['core'], RulePackMetadata)
    assert metadata['core'].stage == 'detect'
    assert metadata['core'].entrypoint.endswith('.packs.core.rules')
    assert 'event_rules' in metadata['core'].capabilities
    assert get_rule_pack_metadata('gui').dependencies == ['cua_debugger']
    assert KeywordRule is LegacyKeywordRule


def test_diagnose_components_share_a_unified_registry() -> None:
    from agentdebug.diagnose import (
        DiagnoseComponentMetadata,
        available_components,
        get_component_metadata,
        list_components,
        load_component,
    )
    from agentdebug.diagnose.attribute import HeuristicAttributor
    from agentdebug.diagnose.detect import HeuristicAnalyzer
    from agentdebug.diagnose.recover import ReflexionSuggestion

    all_ids = set(available_components())
    detect_ids = set(available_components(stage='detect'))
    attribute_ids = set(available_components(stage='attribute'))
    recover_ids = set(available_components(stage='recover'))

    assert {'detect.heuristic', 'detect.llm_judge', 'detect.rules.core'} <= detect_ids
    assert {'attribute.heuristic', 'attribute.deepdebug'} <= attribute_ids
    assert {'recover.deepdebug', 'recover.reflexion'} <= recover_ids
    assert detect_ids | attribute_ids | recover_ids == all_ids

    metadata = get_component_metadata('attribute.heuristic')
    assert isinstance(metadata, DiagnoseComponentMetadata)
    assert metadata.stage == 'attribute'
    assert metadata.entrypoint.endswith(':HeuristicAttributor')

    assert load_component('detect.heuristic') is HeuristicAnalyzer
    assert load_component('attribute.heuristic') is HeuristicAttributor
    assert load_component('recover.reflexion') is ReflexionSuggestion
    assert hasattr(load_component('detect.rules.core'), 'build_event_rules')
    assert list_components(stage='recover')[0].stage == 'recover'


def test_rerun_workflow_plans_and_evaluates_second_stage() -> None:
    from agentdebug.diagnose.pipeline import DiagnosePipeline
    from agentdebug.rerun import RerunResult, RerunWorkflow
    from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

    trajectory = AgentTrajectory(trace_id='trace_rerun', goal='test rerun')
    trajectory.add_event(
        AgentEvent(
            trace_id=trajectory.trace_id,
            event_type=EventType.TOOL_RESULT,
            agent_name='search',
            step_index=1,
            error='missing required parameter query',
        )
    )
    report = DiagnosePipeline.local_default().run(trajectory).report
    report.suggestions = ['Retry the search call with the required query parameter.']

    class SuccessfulExecutor:
        id = 'test_successful_executor'

        def run(self, request):
            rerun = AgentTrajectory(trace_id='trace_rerun_fixed', goal='test rerun')
            rerun.add_event(
                AgentEvent(
                    trace_id=rerun.trace_id,
                    event_type=EventType.TOOL_RESULT,
                    agent_name='search',
                    step_index=1,
                    output={'ok': True},
                )
            )
            return RerunResult(
                request=request,
                trajectory=rerun,
                metadata={'executor': self.id},
            )

    workflow = RerunWorkflow(executor=SuccessfulExecutor())
    plan = workflow.plan(report, trajectory)

    assert plan.request.trace_id == trajectory.trace_id
    assert plan.request.report_id == report.report_id
    assert plan.request.checkpoint.step_index == 1
    assert 'required query parameter' in plan.request.directive.text
    assert plan.approval_required is True

    dry_result = workflow.run(report, trajectory)
    assert dry_result.executed is False
    assert dry_result.evaluation is None

    executed = workflow.run(report, trajectory, execute=True)
    assert executed.executed is True
    assert executed.evaluation is not None
    assert executed.evaluation.result == 'improved'


def test_cli_help_starts() -> None:
    from agentdebug.cli import main
    from agentdebug.cli.commands import diagnose, ingest

    assert callable(main)
    assert callable(diagnose.run)
    assert callable(ingest.run)

    result = subprocess.run(
        [sys.executable, '-m', 'agentdebug.cli', '--help'],
        check=True,
        capture_output=True,
        text=True,
    )

    assert 'diagnose' in result.stdout
    assert 'ingest' in result.stdout


def test_cli_rerun_emits_second_stage_plan(tmp_path: Path) -> None:
    from agentdebug.diagnose.pipeline import DiagnosePipeline
    from agentdebug.schema import (
        AgentEvent,
        AgentTrajectory,
        EventType,
        model_to_json,
    )

    trajectory = AgentTrajectory(trace_id='trace_cli_rerun', goal='test cli rerun')
    trajectory.add_event(
        AgentEvent(
            trace_id=trajectory.trace_id,
            event_type=EventType.ERROR,
            agent_name='agent',
            step_index=2,
            error='tool failed',
        )
    )
    report = DiagnosePipeline.local_default().run(trajectory).report
    report.suggestions = ['Retry after fixing the tool failure.']
    trajectory_path = tmp_path / 'trajectory.json'
    report_path = tmp_path / 'report.json'
    trajectory_path.write_text(model_to_json(trajectory, indent=2), encoding='utf-8')
    report_path.write_text(model_to_json(report, indent=2), encoding='utf-8')

    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'agentdebug.cli',
            'rerun',
            str(report_path),
            '--trajectory',
            str(trajectory_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload['stage'] == 'rerun'
    assert payload['status'] == 'planned'
    assert payload['plan']['request']['trace_id'] == trajectory.trace_id
    assert payload['plan']['request']['checkpoint']['step_index'] == 2
    assert 'Retry after fixing' in payload['plan']['request']['directive']['text']


def test_cli_diagnose_defaults_to_local_heuristic_pipeline() -> None:
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'agentdebug.cli',
            'diagnose',
            'examples/sample_trace.json',
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"trace_id": "trace_sample"' in result.stdout
    assert '"analyzer": "HeuristicAnalyzer"' in result.stdout


def test_poetry_extras_reference_declared_optional_dependencies() -> None:
    pyproject = tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))
    dependencies = pyproject['tool']['poetry']['dependencies']
    extras = pyproject['tool']['poetry']['extras']

    missing = {
        extra: [name for name in names if name not in dependencies]
        for extra, names in extras.items()
    }
    missing = {extra: names for extra, names in missing.items() if names}

    assert missing == {}
    for extra, names in extras.items():
        for name in names:
            assert dependencies[name].get('optional') is True, (extra, name)
