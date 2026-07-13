"""FastAPI routes for the local inspection UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agentdebug.diagnose.detect import HeuristicAnalyzer
from agentdebug.inspect.ui.branch_store import (
    _append_case_record,
    _append_debug_branch_record,
    _case_db_path,
    _debug_branch_db_path,
    _delete_case_record,
    _read_case_records,
    _read_debug_branch_records,
    _write_debug_branch_records,
)
from agentdebug.inspect.ui.services import (
    _build_debug_continuation_context,
    _build_rerun_evaluation,
    _decorate_debug_branch_record,
    _extract_chat_content,
    _extract_json_payload,
    _extract_partial_continuation_payload,
    _normalize_generated_events,
    _request_debug_completion,
    _to_dict,
    build_overview,
)
from agentdebug.inspect.ui.views import render_gui_page, render_page, render_space_page
from agentdebug.runtime import TraceStore
from agentdebug.schema import SEED_FAILURE_MODES

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

    @app.get('/healthz')
    def healthz() -> Dict[str, str]:
        return {'status': 'ok'}

    @app.get('/api/v1/traces')
    def list_traces() -> Dict[str, List[str]]:
        return {'traces': store.list_traces()}

    @app.get('/api/v1/overview')
    def get_overview() -> Dict[str, Any]:
        return build_overview(store)

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
        report = HeuristicAnalyzer().analyze(trajectory)
        trajectory_payload = _to_dict(trajectory)
        report_payload = _to_dict(report)
        primary = report_payload.get('findings', [{}])[0] if report_payload.get('findings') else {}
        failure_mode = primary.get('failure_mode') or {}
        created_at = datetime.now(timezone.utc).isoformat()
        record = {
            'case_id': f'{trace_id}::{created_at}',
            'created_at': created_at,
            'trace_id': trace_id,
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

    @app.get('/gui', response_class=HTMLResponse)
    def gui_page() -> str:
        return render_gui_page()

    @app.get('/trace/{trace_id}', response_class=HTMLResponse)
    def trace_page(trace_id: str) -> str:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            return render_page(store, view='overview')
        return render_page(store, view='trace', trace_id=trace_id)

    @app.get('/trace/{trace_id}/event/{event_id}', response_class=HTMLResponse)
    def trace_event_page(trace_id: str, event_id: str) -> str:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            return render_page(store, view='overview')
        if not any(evt.event_id == event_id for evt in trajectory.events):
            raise HTTPException(
                status_code=404,
                detail=f'unknown event_id {event_id!r} for trace_id {trace_id!r}',
            )
        return render_page(store, view='event', trace_id=trace_id, event_id=event_id)

    @app.get('/api/v1/traces/{trace_id}')
    def get_trace(trace_id: str) -> Dict[str, Any]:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report = HeuristicAnalyzer().analyze(trajectory)
        return {
            'trajectory': _to_dict(trajectory),
            'report': _to_dict(report),
        }

    @app.post('/api/v1/traces/{trace_id}/debug-continuation')
    def create_debug_continuation(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(payload.get('event_id') or '').strip()
        if not event_id:
            raise HTTPException(status_code=400, detail='event_id is required')
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report = HeuristicAnalyzer().analyze(trajectory)
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
        report = HeuristicAnalyzer().analyze(trajectory) if trajectory is not None else None
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
        report = HeuristicAnalyzer().analyze(trajectory) if trajectory is not None else None
        trajectory_payload = _to_dict(trajectory) if trajectory is not None else {'events': []}
        report_payload = _to_dict(report) if report is not None else {'findings': []}
        return {
            'ok': True,
            'session': _decorate_debug_branch_record(updated, trajectory_payload, report_payload),
            'path': str(_debug_branch_db_path()),
        }

    def _run_rerun_from_event(trace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(payload.get('event_id') or '').strip()
        if not event_id:
            raise HTTPException(status_code=400, detail='event_id is required')
        api_url = str(payload.get('api_url') or '').strip()
        api_key = str(payload.get('api_key') or '').strip()
        model = str(payload.get('model') or 'gpt-4o').strip() or 'gpt-4o'
        prompt_text = str(payload.get('prompt_text') or '').strip()
        if not api_url:
            raise HTTPException(status_code=400, detail='api_url is required')
        if not api_key:
            raise HTTPException(status_code=400, detail='api_key is required')
        if not prompt_text:
            raise HTTPException(status_code=400, detail='prompt_text is required')

        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        report = HeuristicAnalyzer().analyze(trajectory)
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

        try:
            llm_response = _request_debug_completion(
                api_url=api_url,
                api_key=api_key,
                model=model,
                prompt_text=prompt_text,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'rerun request failed: {exc}') from exc

        response_text = _extract_chat_content(llm_response)
        parsed_payload = _extract_json_payload(response_text) or _extract_partial_continuation_payload(response_text)
        branch_id = 'branch_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
        generated_trace_id = ''
        if isinstance(parsed_payload, dict):
            generated_trace_id = str(
                parsed_payload.get('trace_id')
                or ((parsed_payload.get('trajectory') or {}).get('trace_id') if isinstance(parsed_payload.get('trajectory'), dict) else '')
                or f'{trace_id}__{branch_id}'
            )
        else:
            generated_trace_id = f'{trace_id}__{branch_id}'
        generated_events = _normalize_generated_events(
            parsed_payload,
            parent_event_id=event_id,
            generated_trace_id=generated_trace_id,
            checkpoint_step_index=checkpoint.get('checkpoint_step_index'),
        )
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
            'event_id': event_id,
            'parent_event_id': event_id,
            'checkpoint_ordinal': checkpoint.get('checkpoint_ordinal'),
            'checkpoint_step_index': checkpoint.get('checkpoint_step_index'),
            'debug_model': model,
            'api_url': api_url,
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


