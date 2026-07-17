"""FastAPI routes for the local inspection UI."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agentdebug.inspect.ui.branch_store import (
    _append_case_record,
    _append_debug_branch_record,
    _case_db_path,
    _debug_branch_db_path,
    _delete_case_record,
    _delete_debug_branch_record,
    _read_case_records,
    _read_debug_branch_records,
    _write_debug_branch_records,
)
from agentdebug.inspect.ui.services import (
    _build_debug_continuation_context,
    _build_rerun_evaluation,
    _decorate_debug_branch_record,
    _resolve_trace_analysis,
    _to_dict,
    _ui_runtime_status,
    build_overview,
)
from agentdebug.inspect.ui.views import render_page, render_space_page
from agentdebug.runtime import TraceStore
from agentdebug.schema import SEED_FAILURE_MODES, model_to_json


def build_app(store: TraceStore) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised in docs
        raise ImportError(
            'AgentDebugX UI requires `fastapi` and `uvicorn`. '
            'Install with `pip install agentdebugx[ui]`.'
        ) from exc

    app = FastAPI(
        title='AgentDebugX',
        description='Local debug console for agent trajectories.',
        version='0.1.0',
    )

    @app.middleware('http')
    async def add_local_ui_security_headers(
        request: Any,
        call_next: Any,
    ) -> Any:
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'self'"
        )
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    @app.get('/healthz')
    def healthz() -> Dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/v1/traces')
    def list_traces() -> Dict[str, List[str]]:
        return {'traces': store.list_traces()}

    @app.get('/api/v1/overview')
    def get_overview() -> Dict[str, Any]:
        return build_overview(store)

    @app.get('/api/v1/status')
    def get_ui_status() -> Dict[str, Any]:
        return _ui_runtime_status()

    @app.get('/api/v1/cases')
    def list_cases() -> Dict[str, Any]:
        cases = list(reversed(_read_case_records()))
        return {
            'path': str(_case_db_path()),
            'count': len(cases),
            'cases': cases,
        }

    @app.post('/api/v1/cases')
    def save_case(payload: Dict[str, Any]) -> Dict[str, Any]:
        trace_id = str(payload.get('trace_id') or '').strip()
        if not trace_id:
            raise HTTPException(status_code=400, detail='trace_id is required')
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report_id = str(payload.get('report_id') or '').strip() or None
        try:
            report = _resolve_trace_analysis(
                store,
                trajectory,
                report_id=report_id,
            )['report']
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        trajectory_payload = _to_dict(trajectory)
        report_payload = _to_dict(report)
        primary = report_payload.get('findings', [{}])[0] if report_payload.get('findings') else {}
        failure_mode = primary.get('failure_mode') or {}
        created_at = datetime.now(timezone.utc).isoformat()
        record = {
            'case_id': f'{trace_id}::{created_at}',
            'created_at': created_at,
            'trace_id': trace_id,
            'report_id': report.report_id,
            'title': payload.get('title') or trace_id,
            'note': payload.get('note') or '',
            'dataset': trajectory_payload.get('metadata', {}).get('task_type') or trajectory_payload.get('framework') or '',
            'model': trajectory_payload.get('metadata', {}).get('llm_model') or trajectory_payload.get('framework') or '',
            'event_count': len(trajectory_payload.get('events') or []),
            'finding_count': len(report_payload.get('findings') or []),
            'root_cause_event_id': report_payload.get('root_cause_event_id'),
            'root_cause_step_index': report_payload.get('root_cause_step_index'),
            'top_family': failure_mode.get('family') or '',
            'top_mode': failure_mode.get('mode_id') or failure_mode.get('name') or '',
            'summary': report_payload.get('summary') or '',
            'trajectory': trajectory_payload,
            'report': report_payload,
        }
        _append_case_record(record)
        return {
            'ok': True,
            'path': str(_case_db_path()),
            'case': record,
        }

    @app.delete('/api/v1/cases/{case_id:path}')
    def delete_case(case_id: str) -> Dict[str, Any]:
        if not _delete_case_record(case_id):
            raise HTTPException(status_code=404, detail=f'unknown case_id: {case_id}')
        return {
            'ok': True,
            'path': str(_case_db_path()),
        }

    @app.get('/', response_class=HTMLResponse)
    def index() -> str:
        return render_page(store, view='overview')

    @app.get('/overview', response_class=HTMLResponse)
    def overview_page() -> str:
        return render_page(store, view='overview')

    @app.get('/space', response_class=HTMLResponse)
    def space_page() -> str:
        return render_space_page(store)

    @app.get('/trace/{trace_id}', response_class=HTMLResponse)
    def trace_page(trace_id: str) -> str:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        return render_page(store, view='trace', trace_id=trace_id)

    @app.get('/trace/{trace_id}/event/{event_id}', response_class=HTMLResponse)
    def trace_event_page(trace_id: str, event_id: str) -> str:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        if not any(evt.event_id == event_id for evt in trajectory.events):
            raise HTTPException(
                status_code=404,
                detail=f'unknown event_id {event_id!r} for trace_id {trace_id!r}',
            )
        return render_page(store, view='event', trace_id=trace_id, event_id=event_id)

    @app.get('/api/v1/traces/{trace_id}')
    def get_trace(
        trace_id: str,
        report_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        try:
            analysis = _resolve_trace_analysis(
                store,
                trajectory,
                report_id=report_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        report = analysis['report']
        return {
            'trajectory': _to_dict(trajectory),
            'report': _to_dict(report),
            'report_source': analysis['report_source'],
            'reports': analysis['reports'],
        }

    @app.post('/api/v1/traces/{trace_id}/debug-continuation')
    def create_debug_continuation(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(payload.get('event_id') or '').strip()
        if not event_id:
            raise HTTPException(status_code=400, detail='event_id is required')
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report_id = str(payload.get('report_id') or '').strip() or None
        try:
            report = _resolve_trace_analysis(
                store,
                trajectory,
                report_id=report_id,
            )['report']
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            return _build_debug_continuation_context(
                trajectory,
                report,
                event_id,
                note=str(payload.get('note') or '').strip(),
                mode=str(payload.get('mode') or 'debug').strip() or 'debug',
                selected_event_override=payload.get('selected_event') if isinstance(payload.get('selected_event'), dict) else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get('/api/v1/traces/{trace_id}/debug-branches')
    def list_debug_branches(trace_id: str) -> Dict[str, Any]:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report = (
            _resolve_trace_analysis(store, trajectory)['report']
            if trajectory is not None
            else None
        )
        trajectory_payload = _to_dict(trajectory) if trajectory is not None else {'events': []}
        report_payload = _to_dict(report) if report is not None else {'findings': []}
        branches = [
            _decorate_debug_branch_record(row, trajectory_payload, report_payload)
            for row in reversed(_read_debug_branch_records())
            if str(row.get('trace_id') or '') == trace_id
        ]
        return {
            'trace_id': trace_id,
            'path': str(_debug_branch_db_path()),
            'branches': branches,
            'sessions': branches,
            'count': len(branches),
        }

    @app.get('/api/v1/traces/{trace_id}/debug-sessions')
    def list_debug_sessions(trace_id: str) -> Dict[str, Any]:
        return list_debug_branches(trace_id)

    @app.patch('/api/v1/traces/{trace_id}/debug-sessions/{session_id:path}')
    def update_debug_session(trace_id: str, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if store.load_trajectory(trace_id) is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        records = _read_debug_branch_records()
        updated: Optional[Dict[str, Any]] = None
        for record in records:
            current_id = str(record.get('session_id') or record.get('branch_id') or '')
            if str(record.get('trace_id') or '') == trace_id and current_id == session_id:
                if isinstance(payload.get('evaluation'), dict):
                    existing = record.get('evaluation') if isinstance(record.get('evaluation'), dict) else {}
                    record['evaluation'] = {
                        **existing,
                        **payload['evaluation'],
                        'manual': True,
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    }
                if payload.get('status'):
                    record['status'] = str(payload.get('status'))
                updated = record
                break
        if updated is None:
            raise HTTPException(status_code=404, detail=f'unknown debug session: {session_id}')
        _write_debug_branch_records(records)
        trajectory = store.load_trajectory(trace_id)
        report = (
            _resolve_trace_analysis(store, trajectory)['report']
            if trajectory is not None
            else None
        )
        trajectory_payload = _to_dict(trajectory) if trajectory is not None else {'events': []}
        report_payload = _to_dict(report) if report is not None else {'findings': []}
        return {
            'ok': True,
            'session': _decorate_debug_branch_record(updated, trajectory_payload, report_payload),
            'path': str(_debug_branch_db_path()),
        }

    @app.delete('/api/v1/traces/{trace_id}/debug-sessions/{session_id:path}')
    def delete_debug_session(trace_id: str, session_id: str) -> Dict[str, Any]:
        if store.load_trajectory(trace_id) is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        if not _delete_debug_branch_record(trace_id, session_id):
            raise HTTPException(
                status_code=404,
                detail=f'unknown debug session: {session_id}',
            )
        return {
            'ok': True,
            'trace_id': trace_id,
            'session_id': session_id,
            'path': str(_debug_branch_db_path()),
        }

    def _run_rerun_from_event(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(payload.get('event_id') or '').strip()
        if not event_id:
            raise HTTPException(status_code=400, detail='event_id is required')
        model = str(payload.get('model') or 'live-runner').strip() or 'live-runner'
        prompt_text = str(payload.get('prompt_text') or '').strip()
        if not prompt_text:
            raise HTTPException(status_code=400, detail='prompt_text is required')
        runner_url = str(os.environ.get('AGENTDEBUG_RUNNER_URL') or '').strip()
        runner_command = str(os.environ.get('AGENTDEBUG_RERUN_COMMAND') or '').strip()
        if not runner_url and not runner_command:
            raise HTTPException(
                status_code=503,
                detail=(
                    'live rerun is not configured; set AGENTDEBUG_RUNNER_URL or '
                    'AGENTDEBUG_RERUN_COMMAND on the UI server'
                ),
            )

        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report_id = str(payload.get('report_id') or '').strip() or None
        try:
            report = _resolve_trace_analysis(
                store,
                trajectory,
                report_id=report_id,
            )['report']
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        report.suggestions = [prompt_text]
        checkpoint_policy = str(
            os.environ.get('AGENTDEBUG_UI_RERUN_POLICY') or 'from_start'
        ).strip()
        if checkpoint_policy not in {'from_start', 'from_event'}:
            raise HTTPException(
                status_code=503,
                detail='AGENTDEBUG_UI_RERUN_POLICY must be from_start or from_event',
            )
        try:
            checkpoint = _build_debug_continuation_context(
                trajectory,
                report,
                event_id,
                note=str(payload.get('note') or '').strip(),
                mode='rerun',
                selected_event_override=payload.get('selected_event') if isinstance(payload.get('selected_event'), dict) else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        from agentdebug.rerun import (
            HttpLiveExecutor,
            ProcessLiveExecutor,
            RerunWorkflow,
        )
        if runner_url:
            token_env = str(
                os.environ.get('AGENTDEBUG_RUNNER_TOKEN_ENV') or ''
            ).strip()
            token = os.environ.get(token_env) if token_env else None
            if token_env and not token:
                raise HTTPException(
                    status_code=503,
                    detail=f'runner token environment variable is not set: {token_env}',
                )
            try:
                timeout = _positive_env_float('AGENTDEBUG_RERUN_TIMEOUT', 1800)
                poll_interval = _positive_env_float(
                    'AGENTDEBUG_RUNNER_POLL_INTERVAL',
                    1,
                )
            except ValueError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            executor = HttpLiveExecutor(
                runner_url,
                trajectory,
                token=token,
                timeout=timeout,
                poll_interval=poll_interval,
            )
        else:
            try:
                timeout = _positive_env_float('AGENTDEBUG_RERUN_TIMEOUT', 1800)
            except ValueError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            executor = ProcessLiveExecutor(
                runner_command,
                trajectory,
                cwd=os.environ.get('AGENTDEBUG_RERUN_CWD'),
                timeout=timeout,
            )
        try:
            workflow_result = RerunWorkflow(executor).run(
                report,
                trajectory,
                execute=True,
                checkpoint_policy=checkpoint_policy,
                checkpoint_event_id=(
                    event_id if checkpoint_policy == 'from_event' else None
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'rerun request failed: {exc}') from exc
        finally:
            close = getattr(executor, 'close', None)
            if callable(close):
                close()

        execution = workflow_result.execution
        if execution is None:  # pragma: no cover - executor contract guard
            raise HTTPException(status_code=502, detail='rerun produced no execution')
        parsed_payload = json.loads(model_to_json(execution.trajectory))
        response_text = str(execution.metadata.get('summary') or '')
        llm_response = {'execution': execution.metadata}
        branch_id = 'branch_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
        generated_trace_id = execution.trajectory.trace_id
        generated_events = [
            _to_dict(event) for event in execution.trajectory.events
        ]
        trajectory_payload = _to_dict(trajectory)
        report_payload = _to_dict(report)
        evaluation = _build_rerun_evaluation(
            trajectory_payload,
            report_payload,
            event_id=event_id,
            generated_events=generated_events,
            checkpoint=checkpoint,
        )
        record = {
            'branch_id': branch_id,
            'session_id': branch_id,
            'trace_id': trace_id,
            'report_id': report.report_id,
            'event_id': event_id,
            'parent_event_id': event_id,
            'checkpoint_ordinal': checkpoint.get('checkpoint_ordinal'),
            'checkpoint_step_index': checkpoint.get('checkpoint_step_index'),
            'requested_checkpoint_policy': checkpoint_policy,
            'debug_model': model,
            'execution_mode': execution.metadata.get('execution_mode'),
            'tools_executed': execution.metadata.get('tools_executed'),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'label': str(payload.get('label') or f'rerun from #{checkpoint.get("checkpoint_ordinal") or "?"}'),
            'run_type': 'rerun_from_event',
            'status': 'completed',
            'prompt_preview': prompt_text[:240] + ('...' if len(prompt_text) > 240 else ''),
            'prompt_text': prompt_text,
            'options': payload.get('options') or {},
            'response_text': response_text,
            'response_json': llm_response,
            'parsed_payload': parsed_payload,
            'generated_trace_id': generated_trace_id,
            'generated_events': generated_events,
            'evaluation': evaluation,
        }
        _append_debug_branch_record(record)
        return {
            'ok': True,
            'trace_id': trace_id,
            'branch_id': branch_id,
            'path': str(_debug_branch_db_path()),
            'branch': record,
        }

    @app.post('/api/v1/traces/{trace_id}/rerun-from-event')
    def run_rerun_from_event(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return _run_rerun_from_event(trace_id, payload)

    @app.post('/api/v1/traces/{trace_id}/debug-run')
    def run_debug_continuation(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return _run_rerun_from_event(trace_id, payload)

    @app.get('/api/v1/traces/{trace_id}/raw')
    def get_trace_raw(trace_id: str) -> JSONResponse:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        return JSONResponse(content=_to_dict(trajectory))

    @app.get('/api/v1/taxonomy')
    def get_taxonomy() -> Dict[str, Any]:
        return {
            'modes': [_to_dict(m) for m in SEED_FAILURE_MODES.values()],
        }

    return app


def _positive_env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be a positive number') from exc
    if value <= 0:
        raise ValueError(f'{name} must be a positive number')
    return value
