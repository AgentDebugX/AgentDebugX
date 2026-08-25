"""FastAPI routes for the local inspection UI."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from agentdebug.inspect.discussion import (
    DiscussionError,
    DiscussionLLMError,
    DiscussionService,
)
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
from agentdebug.inspect.ui.discussion_store import (
    DiscussionNotFoundError,
    DiscussionStoreError,
    DiscussionVersionConflictError,
    SQLiteDiscussionStore,
)
from agentdebug.inspect.ui.services import (
    _build_debug_continuation_context,
    _build_rerun_evaluation,
    _decorate_debug_branch_record,
    _resolve_trace_analysis,
    _to_dict,
    _ui_runtime_status,
    build_overview,
    build_visual_capability,
    resolve_visual_artifact,
)
from agentdebug.inspect.ui.views import render_page, render_space_page
from agentdebug.inspect.ui.llm_convert import schema_payload
from agentdebug.inspect.ui.upload import MAX_UPLOAD_BYTES, import_upload_text
from agentdebug.runtime import TraceStore
from agentdebug.schema import SEED_FAILURE_MODES, model_to_json


def build_app(
    store: TraceStore,
    *,
    run_registry: Optional[Any] = None,
    discussion_store: Optional[SQLiteDiscussionStore] = None,
    discussion_llm_factory: Optional[Any] = None,
) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised in docs
        raise ImportError(
            'AgentDebugX UI requires `fastapi` and `uvicorn`. '
            'Install with `pip install agentdebugx[ui]`.'
        ) from exc
    globals()['_FastAPIRequest'] = Request
    conversations = discussion_store or SQLiteDiscussionStore()

    app = FastAPI(
        title='AgentDebugX',
        description='Local debug console for agent trajectories.',
        version='0.1.0',
    )

    def discussion_session_payload(session: Any) -> Dict[str, Any]:
        trajectory = store.load_trajectory(session.trace_id)
        stale_report = False
        if trajectory is not None:
            try:
                current = _resolve_trace_analysis(store, trajectory)['report']
                stale_report = (
                    DiscussionService(trajectory, current).report_digest
                    != session.report_digest
                )
            except (DiscussionError, ValueError):
                stale_report = True
        report_metadata = session.report_snapshot.get('metadata') or {}
        return {
            'session_id': session.session_id,
            'trace_id': session.trace_id,
            'report_id': session.report_id,
            'report_source': str(
                report_metadata.get('analyzer')
                or report_metadata.get('mode')
                or 'pinned snapshot'
            ),
            'snapshot_digest': session.snapshot_digest,
            'report_digest': session.report_digest,
            'model': session.model,
            'status': session.status,
            'version': session.version,
            'created_at': session.created_at,
            'updated_at': session.updated_at,
            'stale_report': stale_report,
        }

    def require_discussion(trace_id: str, session_id: str) -> Any:
        session = conversations.get_session(session_id)
        if session is None or session.trace_id != trace_id:
            raise HTTPException(status_code=404, detail='discussion not found')
        return session

    def discussion_detail(session: Any) -> Dict[str, Any]:
        return {
            'session': discussion_session_payload(session),
            'messages': [
                message.to_dict()
                for message in conversations.list_messages(session.session_id)
            ],
        }

    def discussion_llm(payload: Dict[str, Any], *, fallback_model: str) -> Any:
        if discussion_llm_factory is not None:
            return discussion_llm_factory(payload)
        from agentdebug.core.llm import OpenAICompatClient

        base_url = str(
            payload.get('base_url')
            or os.environ.get('AGENTDEBUG_LLM_BASE_URL')
            or ''
        ).strip()
        api_key = str(
            payload.get('api_key')
            or os.environ.get('AGENTDEBUG_LLM_API_KEY')
            or ''
        ).strip()
        model = str(
            payload.get('model')
            or os.environ.get('AGENTDEBUG_LLM_MODEL')
            or fallback_model
            or ''
        ).strip()
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {'http', 'https'}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(
                status_code=400,
                detail='Discussion requires a valid HTTP(S) LLM Base URL.',
            )
        if not api_key or not model:
            raise HTTPException(
                status_code=400,
                detail='Discussion requires Base URL, API Key, and Model.',
            )
        normalized_url = base_url.removesuffix('/chat/completions').rstrip('/')
        return OpenAICompatClient(
            base_url=normalized_url,
            api_key=api_key,
            model=model,
            default_max_tokens=2048,
            timeout=90.0,
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

    @app.get('/assets/logo.png', response_class=FileResponse)
    def logo_asset() -> Any:
        logo_path = Path(__file__).resolve().parents[4] / 'docs' / 'assets' / 'logo.png'
        if not logo_path.is_file():
            raise HTTPException(status_code=404, detail='AgentDebugX logo asset not found')
        return FileResponse(logo_path, media_type='image/png')

    @app.get('/assets/robot-avatar.svg', response_class=FileResponse)
    def robot_avatar_asset() -> Any:
        avatar_path = Path(__file__).resolve().parent / 'assets' / 'robot-avatar.svg'
        if not avatar_path.is_file():
            raise HTTPException(status_code=404, detail='AgentDebugX avatar asset not found')
        return FileResponse(avatar_path, media_type='image/svg+xml')

    @app.get('/api/v1/traces')
    def list_traces() -> Dict[str, List[str]]:
        return {'traces': store.list_traces()}

    @app.get('/api/v1/schema')
    def get_upload_schema() -> Dict[str, Any]:
        return schema_payload()

    @app.post('/api/v1/traces/upload')
    async def upload_trace(request: _FastAPIRequest) -> Dict[str, Any]:  # noqa: F821
        raw = await request.body()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail='file too large (>25 MB)')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail='file is not utf-8 text') from exc

        options: Dict[str, Any] = {}
        content_type = str(request.headers.get('content-type') or '')
        if 'application/json' in content_type:
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f'invalid JSON: {exc}') from exc
            if isinstance(candidate, dict) and isinstance(candidate.get('content'), str):
                options = candidate
                text = candidate['content']
        try:
            return import_upload_text(
                store,
                text,
                allow_llm=bool(options.get('allow_llm', True)),
                base_url=str(options.get('base_url') or ''),
                api_key=str(options.get('api_key') or ''),
                model=str(options.get('model') or ''),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        trace_ids = store.list_traces()
        if trace_ids:
            return render_page(store, view='trace', trace_id=trace_ids[0])
        return render_page(store, view='overview')

    @app.get('/overview', response_class=HTMLResponse)
    def overview_page() -> str:
        return render_page(store, view='overview')

    @app.get('/runs/{run_id}', response_class=HTMLResponse)
    def run_page(run_id: str) -> str:
        if run_registry is None:
            raise HTTPException(status_code=404, detail='run registry is not configured')
        try:
            run = run_registry.load_run(run_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f'unknown run_id: {run_id}') from exc
        trace_id = run.artifacts.trace_id
        report_id = run.artifacts.report_id
        if not trace_id or not report_id:
            raise HTTPException(status_code=409, detail='run has no completed trajectory and report')
        trajectory = store.load_trajectory(trace_id)
        report = store.load_report(trace_id, report_id)
        if trajectory is None or report is None:
            raise HTTPException(status_code=409, detail='run artifact consistency error: exact trajectory or report is missing')
        return render_page(store, view='trace', trace_id=trace_id, report_id=report_id)

    @app.get('/api/v1/runs/{run_id}')
    def get_run(run_id: str) -> Dict[str, Any]:
        if run_registry is None:
            raise HTTPException(status_code=404, detail='run registry is not configured')
        try:
            run = run_registry.load_run(run_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=f'unknown run_id: {run_id}') from exc
        payload = _to_dict(run)
        trace_id, report_id = run.artifacts.trace_id, run.artifacts.report_id
        payload['artifacts_consistent'] = bool(
            trace_id and report_id and store.load_trajectory(trace_id)
            and store.load_report(trace_id, report_id)
        )
        if payload['artifacts_consistent']:
            payload['resolved_artifacts'] = {
                'trace_id': trace_id,
                'report_id': report_id,
                'report_source': 'stored',
            }
        return payload

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
            'visual_capability': build_visual_capability(trajectory),
        }

    @app.get(
        '/api/v1/traces/{trace_id}/events/{event_id}/artifacts/{artifact_index}',
        response_class=FileResponse,
    )
    def get_trace_artifact(
        trace_id: str,
        event_id: str,
        artifact_index: int,
    ) -> Any:
        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        try:
            path, media_type = resolve_visual_artifact(
                trajectory,
                event_id,
                artifact_index,
            )
        except (FileNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return FileResponse(path, media_type=media_type)

    @app.get('/api/v1/traces/{trace_id}/discussions')
    def list_trace_discussions(trace_id: str) -> Dict[str, Any]:
        if store.load_trajectory(trace_id) is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        return {
            'trace_id': trace_id,
            'sessions': [
                discussion_session_payload(session)
                for session in conversations.list_sessions(trace_id=trace_id)
            ],
        }

    @app.post('/api/v1/traces/{trace_id}/discussions')
    def create_trace_discussion(
        trace_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
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
        model = str(
            payload.get('model')
            or os.environ.get('AGENTDEBUG_LLM_MODEL')
            or 'not configured'
        ).strip()
        try:
            session = conversations.create_session(
                trajectory,
                report,
                model=model,
            )
        except DiscussionStoreError as exc:
            raise HTTPException(status_code=400, detail=exc.public_message) from exc
        return discussion_detail(session)

    @app.get('/api/v1/traces/{trace_id}/discussions/{session_id}')
    def get_trace_discussion(trace_id: str, session_id: str) -> Dict[str, Any]:
        return discussion_detail(require_discussion(trace_id, session_id))

    @app.delete('/api/v1/traces/{trace_id}/discussions/{session_id}')
    def delete_trace_discussion(
        trace_id: str,
        session_id: str,
    ) -> Dict[str, Any]:
        session = require_discussion(trace_id, session_id)
        try:
            conversations.delete_session(
                session.session_id,
                expected_version=session.version,
            )
        except DiscussionVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.public_message) from exc
        except DiscussionStoreError as exc:
            raise HTTPException(status_code=400, detail=exc.public_message) from exc
        return {'deleted': session_id}

    @app.post('/api/v1/traces/{trace_id}/discussions/{session_id}/messages')
    def send_trace_discussion_message(
        trace_id: str,
        session_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        require_discussion(trace_id, session_id)
        message_text = str(payload.get('message') or '').strip()
        client_message_id = str(payload.get('client_message_id') or '').strip()
        if not message_text or len(message_text) > 8000:
            raise HTTPException(
                status_code=400,
                detail='message must contain between 1 and 8000 characters',
            )
        if not client_message_id:
            raise HTTPException(status_code=400, detail='client_message_id is required')
        try:
            expected_version = int(payload.get('expected_version'))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail='expected_version is required',
            ) from exc

        prior_messages = conversations.list_messages(session_id)
        assistant_client_id = client_message_id + ':assistant'
        if any(
            item.client_message_id == assistant_client_id
            for item in prior_messages
        ):
            return discussion_detail(require_discussion(trace_id, session_id))
        try:
            user_message = conversations.append_message(
                session_id,
                role='user',
                content=message_text,
                expected_version=expected_version,
                client_message_id=client_message_id,
            )
            updated = require_discussion(trace_id, session_id)
            llm = discussion_llm(payload, fallback_model=updated.model)
            service = DiscussionService(
                updated.trajectory,
                updated.report,
                llm,
                model=str(getattr(llm, 'model', '') or updated.model),
            )
            history = [
                {'role': item.role, 'content': item.content}
                for item in prior_messages
                if item.role in {'user', 'assistant'}
            ]
            result = service.discuss(message_text, history=history)
            result_payload = result.to_dict()
            conversations.append_message(
                session_id,
                role='assistant',
                content=result.content,
                expected_version=updated.version,
                citations=result_payload.get('citations') or [],
                proposal=result_payload.get('revision_draft'),
                usage=result.usage,
                client_message_id=assistant_client_id,
            )
        except DiscussionVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=exc.public_message) from exc
        except DiscussionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=exc.public_message) from exc
        except DiscussionLLMError as exc:
            raise HTTPException(status_code=502, detail=exc.public_message) from exc
        except DiscussionError as exc:
            raise HTTPException(status_code=400, detail=exc.public_message) from exc
        except DiscussionStoreError as exc:
            raise HTTPException(status_code=400, detail=exc.public_message) from exc
        _ = user_message
        return discussion_detail(require_discussion(trace_id, session_id))

    @app.get('/api/v1/diagnose/options')
    def get_diagnose_options() -> Dict[str, Any]:
        from agentdebug.cli import legacy as cli_legacy
        from agentdebug.diagnose.detect.rules import available_rule_packs

        llm_args = argparse.Namespace(
            base_url=None,
            api_key=None,
            model=None,
            embedding_model=None,
        )
        base_url = cli_legacy._resolve_llm_option(
            llm_args,
            attr='base_url',
            env_name='AGENTDEBUG_LLM_BASE_URL',
            config_key='base_url',
        )
        api_key = cli_legacy._resolve_llm_option(
            llm_args,
            attr='api_key',
            env_name='AGENTDEBUG_LLM_API_KEY',
            config_key='api_key',
        )
        model = cli_legacy._resolve_llm_option(
            llm_args,
            attr='model',
            env_name='AGENTDEBUG_LLM_MODEL',
            config_key='model',
            default='gemini-3-flash',
        )
        return {
            'modes': list(dict.fromkeys(cli_legacy._DIAGNOSE_MODE_ALIASES.values())),
            'attributors': list(dict.fromkeys(cli_legacy._ATTRIBUTOR_ALIASES.values())),
            'recoveries': list(dict.fromkeys(cli_legacy._RECOVERY_ALIASES.values())),
            'rule_packs': ['auto', *available_rule_packs(), 'all'],
            'llm_configured': bool(base_url and api_key),
            'llm_model': str(model or ''),
            'llm_required_for': {
                'modes': ['judge', 'deep', 'gui-rca'],
                'attributors': sorted(cli_legacy._LLM_ATTRIBUTORS),
                'recoveries': sorted(cli_legacy._LLM_RECOVERIES),
            },
        }

    @app.post('/api/v1/traces/{trace_id}/diagnose')
    def run_diagnose_pipeline(
        trace_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from agentdebug.cli import legacy as cli_legacy

        trajectory = store.load_trajectory(trace_id)
        if trajectory is None:
            raise HTTPException(status_code=404, detail=f'unknown trace_id: {trace_id}')
        body = payload or {}
        try:
            diagnose_mode = cli_legacy._normalize_choice(
                str(body.get('mode') or 'heuristic'),
                cli_legacy._DIAGNOSE_MODE_ALIASES,
                'diagnose mode',
            )
            attributor_mode = cli_legacy._normalize_choice(
                str(body.get('attributor') or 'heuristic'),
                cli_legacy._ATTRIBUTOR_ALIASES,
                'attributor',
            )
            recovery_mode = cli_legacy._normalize_choice(
                str(body.get('recovery') or 'none'),
                cli_legacy._RECOVERY_ALIASES,
                'recovery mode',
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if diagnose_mode == 'deep' and attributor_mode != 'none':
            raise HTTPException(
                status_code=400,
                detail='DeepDebug owns attribution; select attributor=none.',
            )
        if diagnose_mode == 'deep' and recovery_mode not in {'none', 'deepdebug'}:
            raise HTTPException(
                status_code=400,
                detail='DeepDebug recovery must be none or deepdebug.',
            )

        raw_rule_pack = body.get('rule_pack', 'auto')
        rule_pack = raw_rule_pack if isinstance(raw_rule_pack, list) else [raw_rule_pack]
        rule_pack = [str(item) for item in rule_pack if str(item or '').strip()] or ['auto']
        allowed_packs = {'auto', 'core', 'agenterrorbench', 'gui', 'all'}
        invalid_packs = [item for item in rule_pack if item not in allowed_packs]
        if invalid_packs:
            raise HTTPException(status_code=400, detail='unknown rule_pack: ' + ', '.join(invalid_packs))

        args = argparse.Namespace(
            rule_pack=rule_pack,
            model=body.get('model') or None,
            base_url=body.get('base_url') or None,
            api_key=body.get('api_key') or None,
            embedding_model=body.get('embedding_model') or None,
        )
        needs_llm = (
            diagnose_mode in {'judge', 'deep', 'gui-rca'}
            or attributor_mode in cli_legacy._LLM_ATTRIBUTORS
            or recovery_mode in cli_legacy._LLM_RECOVERIES
        )
        llm = cli_legacy._build_llm(args, command_name='local UI diagnose') if needs_llm else None
        if needs_llm and llm is None:
            raise HTTPException(
                status_code=400,
                detail='This pipeline requires an LLM. Configure AGENTDEBUG_LLM_* or provide Base URL, API Key, and Model.',
            )
        started = time.monotonic()
        try:
            report = cli_legacy._run_diagnose_pipeline(
                args,
                trajectory,
                diagnose_mode=diagnose_mode,
                attributor_mode=attributor_mode,
                recovery_mode=recovery_mode,
                llm=llm,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'diagnose pipeline failed: {exc}') from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        report.metadata.update({
            'source': 'diagnose_pipeline',
            'diagnose_mode': diagnose_mode,
            'attributor': attributor_mode,
            'recovery': recovery_mode,
            'rule_pack': rule_pack,
            'duration_ms': duration_ms,
        })
        save_report = getattr(store, 'save_report', None)
        if callable(save_report):
            save_report(report)
        return {
            'trace_id': trace_id,
            'duration_ms': duration_ms,
            'pipeline': {
                'mode': diagnose_mode,
                'attributor': attributor_mode,
                'recovery': recovery_mode,
                'rule_pack': rule_pack,
                'model': args.model or getattr(llm, 'model', None) or '',
                'llm_required': needs_llm,
            },
            'report': _to_dict(report),
            'stored': callable(save_report),
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
        rerun_mode = str(payload.get('rerun_mode') or 'live').strip().lower()
        if rerun_mode not in {'plan_only', 'simulate', 'live'}:
            raise HTTPException(status_code=400, detail=f'unknown rerun_mode: {rerun_mode}')
        model = str(payload.get('model') or 'live-runner').strip() or 'live-runner'
        prompt_text = str(payload.get('prompt_text') or '').strip()
        if not prompt_text:
            raise HTTPException(status_code=400, detail='prompt_text is required')
        runner_url = str(os.environ.get('AGENTDEBUG_RUNNER_URL') or '').strip()
        runner_command = str(os.environ.get('AGENTDEBUG_RERUN_COMMAND') or '').strip()

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
        default_checkpoint_policy = str(
            os.environ.get('AGENTDEBUG_UI_RERUN_POLICY') or 'from_start'
        ).strip()
        requested_checkpoint_policy = str(
            payload.get('checkpoint_policy') or default_checkpoint_policy
        ).strip()
        if requested_checkpoint_policy not in {'from_start', 'from_event'}:
            raise HTTPException(
                status_code=400,
                detail='checkpoint_policy must be from_start or from_event',
            )
        live_checkpoint_policy = requested_checkpoint_policy
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

        if rerun_mode == 'plan_only':
            from agentdebug.rerun import RerunWorkflow

            plan = RerunWorkflow.suggest_only().plan(
                report,
                trajectory,
                checkpoint_policy=requested_checkpoint_policy,
                checkpoint_event_id=(
                    event_id if requested_checkpoint_policy == 'from_event' else None
                ),
            )
            branch_id = 'branch_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
            record = {
                'branch_id': branch_id,
                'session_id': branch_id,
                'trace_id': trace_id,
                'report_id': report.report_id,
                'event_id': event_id,
                'parent_event_id': event_id,
                'checkpoint_ordinal': checkpoint.get('checkpoint_ordinal'),
                'checkpoint_step_index': checkpoint.get('checkpoint_step_index'),
                'requested_checkpoint_policy': requested_checkpoint_policy,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'label': str(payload.get('label') or f'Rerun plan from #{checkpoint.get("checkpoint_ordinal") or "?"}'),
                'run_type': 'rerun_plan',
                'status': 'planned',
                'execution_mode': None,
                'tools_executed': False,
                'prompt_preview': prompt_text[:240] + ('...' if len(prompt_text) > 240 else ''),
                'prompt_text': prompt_text,
                'plan': plan.to_dict(),
                'generated_events': [],
            }
            _append_debug_branch_record(record)
            return {
                'ok': True,
                'trace_id': trace_id,
                'branch_id': branch_id,
                'path': str(_debug_branch_db_path()),
                'mode': 'plan_only',
                'plan': plan.to_dict(),
                'branch': record,
            }

        if rerun_mode == 'simulate':
            from agentdebug.core.llm import OpenAICompatClient
            from agentdebug.rerun import (
                RerunWorkflow,
                RolloutContext,
                SimulatedRerunExecutor,
            )

            base_url = str(payload.get('base_url') or os.environ.get('AGENTDEBUG_LLM_BASE_URL') or '').strip()
            api_key = str(payload.get('api_key') or os.environ.get('AGENTDEBUG_LLM_API_KEY') or '').strip()
            simulation_model = str(payload.get('model') or os.environ.get('AGENTDEBUG_LLM_MODEL') or '').strip()
            if not base_url or not api_key or not simulation_model:
                raise HTTPException(
                    status_code=400,
                    detail='Simulation requires Base URL, API Key, and Model (or AGENTDEBUG_LLM_*).',
                )
            llm = OpenAICompatClient(
                base_url=base_url.removesuffix('/chat/completions').rstrip('/'),
                api_key=api_key,
                model=simulation_model,
                default_max_tokens=8192,
                timeout=180.0,
            )
            executor = SimulatedRerunExecutor(
                llm,
                RolloutContext(
                    trajectory,
                    start_event_id=(
                        event_id if requested_checkpoint_policy == 'from_event' else None
                    ),
                ),
            )
            try:
                workflow_result = RerunWorkflow(
                    executor,
                    allow_simulated=True,
                ).run(
                    report,
                    trajectory,
                    execute=True,
                    checkpoint_policy=requested_checkpoint_policy,
                    checkpoint_event_id=(
                        event_id if requested_checkpoint_policy == 'from_event' else None
                    ),
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f'simulated rerun failed: {exc}') from exc
            execution = workflow_result.execution
            if execution is None:
                raise HTTPException(status_code=502, detail='simulation produced no execution')
            generated_events = [_to_dict(event) for event in execution.trajectory.events]
            evaluation = _build_rerun_evaluation(
                _to_dict(trajectory),
                _to_dict(report),
                event_id=event_id,
                generated_events=generated_events,
                checkpoint=checkpoint,
            )
            branch_id = 'branch_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
            record = {
                'branch_id': branch_id,
                'session_id': branch_id,
                'trace_id': trace_id,
                'report_id': report.report_id,
                'event_id': event_id,
                'parent_event_id': event_id,
                'checkpoint_ordinal': checkpoint.get('checkpoint_ordinal'),
                'checkpoint_step_index': checkpoint.get('checkpoint_step_index'),
                'requested_checkpoint_policy': requested_checkpoint_policy,
                'debug_model': simulation_model,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'label': str(payload.get('label') or f'Simulation from #{checkpoint.get("checkpoint_ordinal") or "?"}'),
                'run_type': 'simulated_rerun',
                'status': 'simulated',
                'execution_mode': execution.execution_mode,
                'tools_executed': False,
                'verified': False,
                'artifact_type': 'hypothetical_trajectory',
                'prompt_preview': prompt_text[:240] + ('...' if len(prompt_text) > 240 else ''),
                'prompt_text': prompt_text,
                'generated_trace_id': execution.trajectory.trace_id,
                'generated_events': generated_events,
                'execution_metadata': execution.metadata,
                'workflow': workflow_result.to_dict(),
                'evaluation': evaluation,
            }
            _append_debug_branch_record(record)
            return {
                'ok': True,
                'trace_id': trace_id,
                'branch_id': branch_id,
                'path': str(_debug_branch_db_path()),
                'mode': 'simulate',
                'branch': record,
            }

        mcp_config = payload.get('mcp') if isinstance(payload.get('mcp'), dict) else None
        if mcp_config is not None:
            from agentdebug.core.llm import OpenAICompatClient
            from agentdebug.inspect.ui.mcp_rerun import McpRerunError, run_mcp_rerun

            base_url = str(payload.get('base_url') or os.environ.get('AGENTDEBUG_LLM_BASE_URL') or '').strip()
            api_key = str(payload.get('api_key') or os.environ.get('AGENTDEBUG_LLM_API_KEY') or '').strip()
            llm_model = str(payload.get('model') or os.environ.get('AGENTDEBUG_LLM_MODEL') or '').strip()
            if not base_url or not api_key or not llm_model:
                raise HTTPException(
                    status_code=400,
                    detail='MCP rerun requires Base URL, API Key, and Model (or AGENTDEBUG_LLM_*).',
                )
            try:
                llm = OpenAICompatClient(
                    base_url=base_url.removesuffix('/chat/completions').rstrip('/'),
                    api_key=api_key,
                    model=llm_model,
                    default_max_tokens=2048,
                    timeout=90.0,
                )
                mcp_result = run_mcp_rerun(
                    trajectory=trajectory,
                    checkpoint_context=checkpoint,
                    directive=prompt_text,
                    mcp_config=mcp_config,
                    llm=llm,
                )
            except McpRerunError as exc:
                raise HTTPException(status_code=400, detail=f'MCP rerun rejected: {exc}') from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f'MCP rerun failed: {exc}') from exc

            branch_id = 'branch_' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
            generated_events = mcp_result.get('generated_events') or []
            evaluation = _build_rerun_evaluation(
                _to_dict(trajectory),
                _to_dict(report),
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
                'requested_checkpoint_policy': requested_checkpoint_policy,
                'debug_model': llm_model,
                'execution_mode': 'live_mcp',
                'tools_executed': mcp_result.get('tools_executed'),
                'tool_call_count': mcp_result.get('tool_call_count'),
                'mcp_server_host': mcp_result.get('mcp_server_host'),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'label': str(payload.get('label') or f'MCP rerun from #{checkpoint.get("checkpoint_ordinal") or "?"}'),
                'run_type': 'mcp_rerun',
                'status': 'completed',
                'prompt_preview': prompt_text[:240] + ('...' if len(prompt_text) > 240 else ''),
                'prompt_text': prompt_text,
                'generated_trace_id': f'{trace_id}__{branch_id}',
                'generated_events': generated_events,
                'transcript': mcp_result.get('transcript') or [],
                'elapsed_ms': mcp_result.get('elapsed_ms'),
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

        if not runner_url and not runner_command:
            raise HTTPException(
                status_code=503,
                detail=(
                    'live rerun is not configured; set AGENTDEBUG_RUNNER_URL or '
                    'AGENTDEBUG_RERUN_COMMAND on the UI server'
                ),
            )

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
                checkpoint_policy=live_checkpoint_policy,
                checkpoint_event_id=(
                    event_id if live_checkpoint_policy == 'from_event' else None
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
            'requested_checkpoint_policy': live_checkpoint_policy,
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
