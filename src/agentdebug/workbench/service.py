"""Python-first orchestration for one supplied trajectory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from agentdebug.batch.workflow import BatchRecord, expand_batch_input
from agentdebug.cli.legacy import _build_llm, _load_trajectory_file, _run_diagnose_pipeline
from agentdebug.ingest.adapters.importers import (
    convert_directory, convert_file, convert_payload,
)
from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore
from agentdebug.runtime.storage import trajectory_from_jsonl_record
from agentdebug.schema import AgentTrajectory
from agentdebug.schema.models import model_to_dict

from .models import (
    BatchRunItem, BatchRunResult, DebugRun, RunAction, RunArtifactRefs,
    RunError, RunInput, RunRequest, ResolvedPipeline, RunResult, RunStatus,
    RunWarning,
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
        input=RunInput(
            reference=request.input_reference,
            trajectory_id=request.input_trajectory_id,
        ),
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


def execute_run(
    request: RunRequest, *, trajectory: Optional[AgentTrajectory] = None,
) -> RunResult:
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
        trajectory = trajectory or _load_input(request, store)
        run.input.detected_format = str(trajectory.metadata.get('source_format') or trajectory.framework or 'agenttrajectory')
        run.provenance['input_snapshot'] = {
            'trace_id': trajectory.trace_id,
            'sha256': hashlib.sha256(
                json.dumps(
                    model_to_dict(trajectory), sort_keys=True, separators=(',', ':')
                ).encode('utf-8')
            ).hexdigest(),
            'event_count': len(trajectory.events),
            'last_event_id': (
                trajectory.events[-1].event_id if trajectory.events else None
            ),
        }
        store.save_trajectory(trajectory)
        run.artifacts.trace_id = trajectory.trace_id
        capture_host = trajectory.metadata.get('capture_host')
        capture_session_id = trajectory.metadata.get('capture_host_session_id')
        if capture_host and capture_session_id:
            registry.bind_session(
                run.run_id, str(capture_host), str(capture_session_id)
            )
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
        run.result = model_to_dict(report)
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


def execute_batch_run(request: RunRequest) -> BatchRunResult:
    """Run one durable workbench workflow per independent input record."""

    if request.input_trajectory_id:
        raise ValueError('--batch and --trajectory-id cannot be used together')
    records = expand_batch_input(request.input_reference)
    items: list[BatchRunItem] = []
    succeeded = 0
    for record in records:
        try:
            item_request = _copy_request_for_record(request, record)
            if request.plan_only:
                result = plan_run(item_request)
            else:
                trajectory = _convert_batch_record(record, request)
                result = execute_run(item_request, trajectory=trajectory)
            item = BatchRunItem(
                record_id=record.record_id,
                source=record.source,
                line_number=record.line_number,
                status=result.status,
                result=result,
            )
            if result.status in {'completed', 'planned'}:
                succeeded += 1
            else:
                item.errors = list(result.errors)
        except Exception as exc:
            item = BatchRunItem(
                record_id=record.record_id,
                source=record.source,
                line_number=record.line_number,
                status='failed',
                errors=[RunError(
                    code='invalid_input', phase='ingest', message=str(exc),
                )],
            )
        items.append(item)
    failed = len(items) - succeeded
    if request.plan_only and not failed:
        status: RunStatus = 'planned'
    elif not failed:
        status = 'completed'
    elif not succeeded:
        status = 'failed'
    else:
        status = 'partial'
    return BatchRunResult(
        status=status,
        input=request.input_reference,
        total=len(items),
        succeeded=succeeded,
        failed=failed,
        items=items,
    )


def _convert_batch_record(
    record: BatchRecord, request: RunRequest,
) -> AgentTrajectory:
    if record.parse_error:
        raise ValueError(record.parse_error)
    payload = record.payload
    if isinstance(payload, dict) and isinstance(payload.get('full_trajectory'), str):
        trajectory = trajectory_from_jsonl_record(
            json.dumps(payload), max((record.line_number or 1) - 1, 0),
        )
    else:
        trajectory = convert_payload(
            payload, format=request.format_override or 'auto',
        )
    trajectory.metadata.update({
        'batch_record_id': record.record_id,
        'batch_source': record.source,
        'batch_line_number': record.line_number,
    })
    return trajectory


def _copy_request_for_record(
    request: RunRequest, record: BatchRecord,
) -> RunRequest:
    trajectory_id = None
    if isinstance(record.payload, dict):
        trajectory_id = next((
            str(record.payload[key])
            for key in ('trajectory_id', 'task_id', 'trace_id', 'id')
            if record.payload.get(key) is not None
        ), None)
    changes = {
        'input_reference': record.source,
        'input_trajectory_id': trajectory_id or record.record_id,
    }
    copier = getattr(request, 'model_copy', None)
    return copier(update=changes) if callable(copier) else request.copy(update=changes)


def _load_input(request: RunRequest, store: Any) -> AgentTrajectory:
    path = Path(request.input_reference).expanduser()
    if path.exists():
        if path.is_dir():
            return convert_directory(
                path, format=request.format_override or 'auto',
            )
        if path.suffix.lower() == '.jsonl':
            selected = _load_dataset_trajectory(path, request.input_trajectory_id)
            if selected is not None:
                return selected
        if request.format_override and request.format_override != 'auto':
            return convert_file(path, format=request.format_override)
        return _load_trajectory_file(path)
    trajectory = store.load_trajectory(request.input_reference)
    if trajectory is None:
        raise ValueError(f'input is neither an existing path nor a stored trace_id: {request.input_reference}')
    return trajectory


def _load_dataset_trajectory(
    path: Path, trajectory_id: Optional[str],
) -> Optional[AgentTrajectory]:
    """Select one AgentErrorBench record without turning ``run`` into batch."""

    candidates: list[str] = []
    selected: Optional[AgentTrajectory] = None
    is_dataset = False
    with path.open('r', encoding='utf-8') as handle:
        for index, line in enumerate(handle):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return None
            if not isinstance(payload, dict) or not isinstance(
                payload.get('full_trajectory'), str
            ):
                return None
            is_dataset = True
            source_id = str(
                payload.get('trajectory_id')
                or payload.get('task_id')
                or payload.get('trace_id')
                or payload.get('id')
                or f'row-{index + 1}'
            )
            candidates.append(source_id)
            if trajectory_id is None:
                if selected is None:
                    selected = trajectory_from_jsonl_record(line, index)
                continue
            if source_id == trajectory_id:
                return trajectory_from_jsonl_record(line, index)
            if trajectory_id.startswith('aeb_'):
                candidate = trajectory_from_jsonl_record(line, index)
                if candidate.trace_id == trajectory_id:
                    return candidate
    if not is_dataset:
        return None
    if trajectory_id is not None:
        raise ValueError(
            f'trajectory_id {trajectory_id!r} was not found in {path}; '
            f'available: {", ".join(candidates)}'
        )
    if len(candidates) > 1:
        raise ValueError(
            f'{path} contains {len(candidates)} trajectories; select one with '
            f'--trajectory-id. Available: {", ".join(candidates)}'
        )
    return selected


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
