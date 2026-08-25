"""Python-first orchestration for one supplied trajectory."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agentdebug.cli.legacy import _build_llm, _load_trajectory_file, _run_diagnose_pipeline
from agentdebug.ingest.adapters.importers import convert_file
from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore
from agentdebug.schema import AgentTrajectory

from .models import (
    DebugRun, RunAction, RunArtifactRefs, RunError, RunInput, RunRequest,
    ResolvedPipeline, RunResult, RunStatus, RunWarning,
)
from .profiles import resolve_pipeline
from .registry import RunRegistry


def _pipeline(request: RunRequest) -> ResolvedPipeline:
    return resolve_pipeline(
        request.profile,
        format_override=request.format_override,
        diagnoser_override=request.diagnoser_override,
        attributor_override=request.attributor_override,
        recovery_override=request.recovery_override,
    )


def _initial_run(request: RunRequest, *, status: RunStatus) -> DebugRun:
    pipeline = _pipeline(request)
    return DebugRun(
        status=status,
        input=RunInput(reference=request.input_reference),
        requested_profile=request.profile,
        resolved_pipeline=pipeline,
        artifacts=RunArtifactRefs(
            store_type=request.store_type, store_path=str(Path(request.store_path).expanduser().resolve())
        ),
        provenance={'orchestrator': 'agentdebug.workbench', 'llm_requested': pipeline.llm_required},
    )


def plan_run(request: RunRequest) -> RunResult:
    """Resolve a workflow without ingesting, diagnosing, or persisting a report."""
    run = _initial_run(request, status='planned')
    RunRegistry(request.run_root).create_run(run)
    return RunResult.from_run(run)


def execute_run(request: RunRequest) -> RunResult:
    if request.plan_only:
        return plan_run(request)
    registry = RunRegistry(request.run_root)
    run = registry.create_run(_initial_run(request, status='running'))
    store = (
        SQLiteTraceStore(run.artifacts.store_path)
        if request.store_type == 'sqlite'
        else JsonlTraceStore(run.artifacts.store_path)
    )
    try:
        trajectory = _load_input(request, store)
        run.input.detected_format = str(trajectory.metadata.get('source_format') or trajectory.framework or 'agenttrajectory')
        store.save_trajectory(trajectory)
        run.artifacts.trace_id = trajectory.trace_id
        registry.update_run(run)
    except Exception as exc:
        run.status = 'failed'
        run.errors.append(RunError(code='invalid_input', phase='ingest', message=str(exc)))
        registry.update_run(run)
        return RunResult.from_run(run)

    try:
        _validate_trajectory_pipeline(trajectory, run)
    except ValueError as exc:
        run.status = 'partial'
        run.errors.append(RunError(code='incompatible_pipeline', phase='validate', message=str(exc)))
        registry.update_run(run)
        return RunResult.from_run(run)

    args = _diagnose_namespace()
    pipeline = run.resolved_pipeline
    llm = None
    if pipeline.llm_required:
        llm = _build_llm(args, command_name='run')
        if llm is None:
            run.status = 'partial'
            run.errors.append(RunError(code='llm_unavailable', phase='diagnose', message='The selected profile requires configured LLM credentials.'))
            run.actions = [RunAction(action='retry_with_local_profile', description='Retry with quick or standard.')]
            registry.update_run(run)
            return RunResult.from_run(run)
    try:
        report = _run_diagnose_pipeline(
            args, trajectory,
            diagnose_mode=pipeline.diagnoser.value,
            attributor_mode=pipeline.attributor.value,
            recovery_mode=pipeline.recovery.value,
            llm=llm,
        )
        report.metadata.update({
            'debug_run_id': run.run_id,
            'requested_profile': request.profile,
            'resolved_pipeline': _dump(pipeline),
        })
        store.save_report(report)
        run.artifacts.report_id = report.report_id
        run.candidate_root_cause = _root_cause(report)
        run.top_evidence = list(report.findings[0].evidence[:3]) if report.findings else []
        run.provenance.update({
            'analyzer': report.metadata.get('analyzer'),
            'model': report.metadata.get('model'),
            'report_metadata': report.metadata,
        })
        run.status = 'completed'
        run.actions = [
            RunAction(action='open_ui', description='Inspect the exact stored trajectory and report.'),
            RunAction(action='analyze_deeper', description='Run an explicitly selected LLM-backed profile.'),
            RunAction(action='prepare_rerun', description='Prepare a rerun only after separate authorization.'),
        ]
        registry.update_run(run)
    except Exception as exc:
        run.status = 'partial'
        run.errors.append(RunError(code='diagnosis_failed', phase='diagnose', message=str(exc)))
        registry.update_run(run)
        return RunResult.from_run(run)

    if request.ui:
        try:
            from agentdebug.inspect.ui.manager import ensure_ui
            handle = ensure_ui(run.run_id, run_registry=registry)
            if handle.status == 'ready':
                run.ui_url = handle.run_url
            else:
                run.warnings.append(RunWarning(code='ui_unavailable', phase='ui', message=handle.error or 'UI did not become ready'))
        except Exception as exc:
            run.warnings.append(RunWarning(code='ui_unavailable', phase='ui', message=str(exc)))
        registry.update_run(run)
    return RunResult.from_run(run)


def _load_input(request: RunRequest, store: Any) -> AgentTrajectory:
    path = Path(request.input_reference).expanduser()
    if path.exists():
        if request.format_override and request.format_override != 'auto':
            return convert_file(path, format=request.format_override)
        return _load_trajectory_file(path)
    trajectory = store.load_trajectory(request.input_reference)
    if trajectory is None:
        raise ValueError(f'input is neither an existing path nor a stored trace_id: {request.input_reference}')
    return trajectory


def _diagnose_namespace() -> argparse.Namespace:
    return argparse.Namespace(
        rule_pack=None, base_url=None, api_key=None, model=None,
        embedding_model=None, llm_timeout=None,
    )


def _validate_trajectory_pipeline(trajectory: AgentTrajectory, run: DebugRun) -> None:
    if run.resolved_pipeline.diagnoser.value != 'gui-rca':
        return
    source_format = str(
        trajectory.metadata.get('source_format') or trajectory.framework or ''
    ).lower()
    if source_format not in {'osworld', 'cua'}:
        raise ValueError(
            'gui-rca requires an OSWorld/CUA trajectory; '
            f'detected {source_format or "unknown"!r}'
        )


def _root_cause(report: Any) -> Any:
    if not report.findings and not report.root_cause_event_id:
        return None
    finding = report.findings[0] if report.findings else None
    analyzer = str(report.metadata.get('analyzer') or '').lower()
    source = 'deterministic_analyzer' if 'heuristic' in analyzer else 'llm_analyzer'
    return {
        'event_id': report.root_cause_event_id or getattr(finding, 'event_id', None),
        'step_index': report.root_cause_step_index or getattr(finding, 'step_index', None),
        'summary': getattr(finding, 'message', None) or report.summary,
        'source': source,
    }


def _dump(model: Any) -> dict[str, Any]:
    dumper = getattr(model, 'model_dump', None)
    return dumper(mode='json') if callable(dumper) else model.dict()
