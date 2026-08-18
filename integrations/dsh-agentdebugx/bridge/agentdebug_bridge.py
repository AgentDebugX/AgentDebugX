"""JSON-lines bridge from DeepSeek Harness sessions to AgentDebugX.

This file belongs to the external DSH plugin. AgentDebugX itself stays
unmodified and is consumed only through its existing Python APIs.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, get_args

from agentdebug.diagnose.pipeline import DiagnosePipeline
from agentdebug.ingest.adapters.importers import convert_directory, convert_file
from agentdebug.runtime.storage import SQLiteTraceStore
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    EventType,
    model_to_dict,
)


JsonObject = Dict[str, Any]


def _safe_text(value: str) -> str:
    """Replace code points that cannot be encoded as UTF-8.

    Harness sessions can carry lone surrogates when a tool result splits a
    multi-byte character, and both pydantic's JSON serializer and this bridge's
    stdout reject them. Losing one character beats losing the whole trace.
    """

    try:
        value.encode('utf-8')
        return value
    except UnicodeEncodeError:
        return value.encode('utf-8', 'replace').decode('utf-8')


def _sanitize(value: Any) -> Any:
    """Return ``value`` with every nested string made UTF-8 encodable."""

    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {_safe_text(str(key)): _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    return value


def _timestamp(milliseconds: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc)


def _trace_id(session_id: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9_.-]+', '_', session_id).strip('_')
    return f'dsh_{normalized or "session"}'


def _event_id(trace_id: str, seq: Any) -> str:
    try:
        suffix = str(int(seq))
    except (TypeError, ValueError):
        suffix = re.sub(r'[^A-Za-z0-9_-]+', '_', str(seq))
    return f'{trace_id}_evt_{suffix}'


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_content_text(item) for item in value]
        return '\n'.join(part for part in parts if part)
    if isinstance(value, dict):
        if isinstance(value.get('text'), str):
            return value['text']
        if 'content' in value:
            return _content_text(value['content'])
        if 'message' in value:
            return _content_text(value['message'])
    return ''


def _tool_result_details(data: JsonObject) -> tuple[Optional[str], Any, Optional[str]]:
    message = data.get('message')
    source = message.get('source', {}) if isinstance(message, dict) else {}
    call_id = source.get('callId') if isinstance(source, dict) else None
    blocks = message.get('content', []) if isinstance(message, dict) else []
    error: Optional[str] = None
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict) or block.get('type') != 'tool-result':
            continue
        call_id = call_id or block.get('toolCallId')
        if block.get('isError') is True:
            error = _content_text(block.get('content')) or 'Tool execution failed.'
    explicit_error = data.get('error')
    if isinstance(explicit_error, dict):
        error = str(explicit_error.get('message') or explicit_error.get('code') or error)
    return str(call_id) if call_id is not None else None, message, error


def session_to_trajectory(snapshot: JsonObject) -> AgentTrajectory:
    """Convert a public Harness session snapshot into AgentDebugX IR."""
    snapshot = _sanitize(snapshot)
    session_id = str(snapshot.get('id') or 'session')
    header = snapshot.get('header') if isinstance(snapshot.get('header'), dict) else {}
    events = snapshot.get('events') if isinstance(snapshot.get('events'), list) else []
    trace_id = _trace_id(session_id)
    started_at = _timestamp(events[0].get('time')) if events else datetime.now(timezone.utc)
    ended_at: Optional[datetime] = None
    goal: Optional[str] = None
    normalized: List[AgentEvent] = []
    tool_calls: Dict[str, str] = {}
    skipped_chunks = 0

    for raw in events:
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get('type') or '')
        if event_type.startswith('agentdebug/'):
            continue
        if event_type == 'assistant/chunk':
            skipped_chunks += 1
            continue

        seq = raw.get('seq', len(normalized))
        data = raw.get('data') if isinstance(raw.get('data'), dict) else {}
        turn = data.get('turn')
        step = data.get('step')
        try:
            step_index = int(step) if step is not None else None
        except (TypeError, ValueError):
            step_index = None
        metadata: JsonObject = {
            'source': 'deepseek-harness',
            'dsh_session_id': session_id,
            'dsh_event_type': event_type,
            'dsh_seq': seq,
            'dsh_turn': turn,
            'dsh_step': step,
            'native': data,
        }
        common: JsonObject = {
            'event_id': _event_id(trace_id, seq),
            'trace_id': trace_id,
            'agent_name': str(snapshot.get('agentId') or 'dsh-agent'),
            'step_index': step_index,
            'timestamp': _timestamp(raw.get('time')),
            'metadata': metadata,
        }

        if event_type == 'turn/start':
            normalized.append(AgentEvent(event_type=EventType.RUN_START, **common))
        elif event_type == 'turn/end':
            reason = data.get('reason')
            error = None
            if isinstance(reason, dict) and reason.get('kind') == 'error':
                error = _content_text(reason.get('error')) or json.dumps(
                    reason.get('error'), ensure_ascii=False
                )
            normalized.append(
                AgentEvent(
                    event_type=EventType.ERROR if error else EventType.AGENT_STEP,
                    output=reason,
                    error=error,
                    **common,
                )
            )
            ended_at = _timestamp(raw.get('time'))
        elif event_type in {'step/start', 'step/end'}:
            normalized.append(
                AgentEvent(event_type=EventType.AGENT_STEP, output=data, **common)
            )
        elif event_type == 'user/message':
            message_text = _content_text(data)
            if goal is None and message_text:
                goal = message_text
            normalized.append(
                AgentEvent(
                    event_type=EventType.OBSERVATION,
                    input=data,
                    **common,
                )
            )
        elif event_type in {'request/header', 'request/context'}:
            normalized.append(
                AgentEvent(event_type=EventType.LLM_CALL, input=data, **common)
            )
        elif event_type == 'assistant/message':
            normalized.append(
                AgentEvent(event_type=EventType.LLM_RESPONSE, output=data, **common)
            )
        elif event_type == 'tool/call':
            call_id = str(data.get('callId') or '')
            arguments: Any = data.get('arguments')
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            event = AgentEvent(
                event_type=EventType.TOOL_CALL,
                module=str(data.get('name') or '') or None,
                input=arguments,
                **common,
            )
            normalized.append(event)
            if call_id:
                tool_calls[call_id] = event.event_id
        elif event_type == 'tool/result':
            call_id, output, error = _tool_result_details(data)
            normalized.append(
                AgentEvent(
                    event_type=EventType.ERROR if error else EventType.TOOL_RESULT,
                    parent_event_id=tool_calls.get(call_id or ''),
                    output=output,
                    error=error,
                    **common,
                )
            )
        elif event_type == 'feedback/record':
            normalized.append(
                AgentEvent(event_type=EventType.HUMAN_FEEDBACK, input=data, **common)
            )
        else:
            normalized.append(
                AgentEvent(event_type=EventType.OBSERVATION, output=data, **common)
            )

    return AgentTrajectory(
        trace_id=trace_id,
        task_id=session_id,
        goal=goal,
        framework='deepseek-harness',
        started_at=started_at,
        ended_at=ended_at,
        metadata={
            'source': 'deepseek-harness',
            'session_id': session_id,
            'session_header': header,
            'skipped_assistant_chunks': skipped_chunks,
        },
        events=normalized,
    )


def _summary(report: Any, dashboard_url: Optional[str]) -> JsonObject:
    result: JsonObject = {
        'traceId': report.trace_id,
        'reportId': report.report_id,
        'summary': report.summary,
        'suggestions': list(report.suggestions),
        'findingCount': len(report.findings),
    }
    optional = {
        'rootCauseEventId': report.root_cause_event_id,
        'rootCauseAgent': report.root_cause_agent,
        'rootCauseStepIndex': report.root_cause_step_index,
        'dashboardUrl': dashboard_url,
    }
    result.update({key: value for key, value in optional.items() if value is not None})
    return result


def _store(path: str) -> SQLiteTraceStore:
    return SQLiteTraceStore(path)


def _allowed_path(raw_path: Any, raw_roots: Any) -> Path:
    path = Path(str(raw_path)).expanduser().resolve()
    roots = raw_roots if isinstance(raw_roots, list) else []
    for raw_root in roots:
        root = Path(str(raw_root)).expanduser().resolve()
        if path == root or root in path.parents:
            return path
    raise PermissionError(
        f'trace path is outside configured traceRoots: {path}'
    )


def _recorded_outcome(trajectory: AgentTrajectory) -> Optional[JsonObject]:
    """Return the outcome the trace itself recorded, when the source kept one.

    Heuristic detection reasons over events, so a benchmark trace scored as a
    failure can still yield zero findings. Callers need the recorded outcome to
    avoid reading "no findings" as "the task succeeded".
    """

    metadata = trajectory.metadata or {}
    outcome: JsonObject = {}
    status = metadata.get('status')
    if isinstance(status, str):
        outcome['status'] = status
    score = metadata.get('result_score')
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        outcome['resultScore'] = float(score)
    infeasible = metadata.get('is_infeasible')
    if isinstance(infeasible, bool):
        outcome['isInfeasible'] = infeasible
    return outcome or None


def _diagnose_trajectory(
    trajectory: AgentTrajectory,
    *,
    store_path: str,
    dashboard_url: Optional[str],
) -> JsonObject:
    pipeline = DiagnosePipeline.local_default()
    report = pipeline.run(trajectory).report
    store = _store(store_path)
    store.save_trajectory(trajectory)
    store.save_report(report)
    summary = _summary(report, dashboard_url)
    outcome = _recorded_outcome(trajectory)
    if outcome is not None:
        summary['recordedOutcome'] = outcome
    return {
        'summary': summary,
        'report': model_to_dict(report),
    }


def _installed_version() -> str:
    try:
        return version('agentdebugx')
    except PackageNotFoundError:
        return 'unknown'


def _capabilities() -> JsonObject:
    """Describe the AgentDebugX build that is actually installed.

    Read from the live registries rather than a copied list, so the capability
    answer cannot drift from the package the bridge imports.
    """

    payload: JsonObject = {'agentdebugxVersion': _installed_version()}

    try:
        from agentdebug.ingest.adapters.importers import FormatName

        payload['ingestFormats'] = [str(name) for name in get_args(FormatName)]
    except Exception as exc:  # optional surface: report instead of failing
        payload['ingestFormatsError'] = str(exc)

    try:
        from agentdebug.diagnose.registry import list_components

        payload['diagnoseComponents'] = [
            {
                'id': component.id,
                'stage': component.stage,
                'name': component.name,
                'enabledByDefault': component.enabled_by_default,
                'requiresLlm': 'llm_client' in component.dependencies,
            }
            for component in list_components()
        ]
    except Exception as exc:
        payload['diagnoseComponentsError'] = str(exc)

    try:
        from agentdebug.runtime.gui_taxonomy import list_gui_failure_modes

        payload['guiTaxonomyModeCount'] = len(list_gui_failure_modes())
    except Exception:
        payload['guiTaxonomyModeCount'] = None

    return payload


def handle(method: str, params: JsonObject) -> Any:
    if method == 'status':
        return {'ok': True, 'agentdebugxVersion': _installed_version()}

    if method == 'capabilities':
        return _capabilities()

    if method == 'ingest_snapshot':
        trajectory = session_to_trajectory(params['session'])
        store = _store(str(params['store']))
        store.save_trajectory(trajectory)
        return {
            'traceId': trajectory.trace_id,
            'eventCount': len(trajectory.events),
            'lastEventId': trajectory.events[-1].event_id if trajectory.events else None,
        }

    if method == 'diagnose':
        mode = str(params.get('mode') or 'heuristic')
        if mode != 'heuristic':
            raise ValueError(
                f'unsupported external bridge mode {mode!r}; first release supports heuristic'
            )
        trajectory = session_to_trajectory(params['session'])
        return _diagnose_trajectory(
            trajectory,
            store_path=str(params['store']),
            dashboard_url=params.get('dashboardUrl'),
        )

    if method == 'diagnose_path':
        mode = str(params.get('mode') or 'heuristic')
        if mode != 'heuristic':
            raise ValueError(
                f'unsupported external bridge mode {mode!r}; first release supports heuristic'
            )
        path = _allowed_path(params['path'], params.get('traceRoots'))
        trace_format = str(params.get('format') or 'auto')
        trajectory = (
            convert_directory(path, format=trace_format)
            if path.is_dir()
            else convert_file(path, format=trace_format)
        )
        return _diagnose_trajectory(
            trajectory,
            store_path=str(params['store']),
            dashboard_url=params.get('dashboardUrl'),
        )

    if method == 'get_report':
        report_id = str(params['reportId'])
        for report in _store(str(params['store'])).list_reports():
            if report.report_id == report_id:
                return model_to_dict(report)
        raise KeyError(f'unknown report id: {report_id}')

    raise ValueError(f'unknown bridge method: {method}')


def _encode_response(response: JsonObject) -> str:
    """Encode one response, degrading to an error rather than raising.

    A single unencodable byte in a result must not take the bridge down for the
    rest of the session, so encoding failures are reported over the same
    protocol instead of escaping the serve loop.
    """

    try:
        return json.dumps(_sanitize(response), ensure_ascii=False) + '\n'
    except Exception as exc:
        fallback = {
            'id': response.get('id'),
            'error': {
                'type': 'ResponseEncodingError',
                'message': _safe_text(str(exc)),
            },
        }
        return json.dumps(fallback, ensure_ascii=True) + '\n'


def serve(lines: Iterable[str] = sys.stdin) -> int:
    for line in lines:
        if not line.strip():
            continue
        request: JsonObject = {}
        try:
            request = json.loads(line)
            request_id = request.get('id')
            method = str(request.get('method') or '')
            params = request.get('params')
            if not isinstance(params, dict):
                params = {}
            result = handle(method, params)
            response = {'id': request_id, 'result': result}
        except Exception as exc:  # protocol boundary: convert every failure
            response = {
                'id': request.get('id'),
                'error': {'type': type(exc).__name__, 'message': _safe_text(str(exc))},
            }
        try:
            sys.stdout.write(_encode_response(response))
            sys.stdout.flush()
        except Exception:
            # stdout is the only channel back to the plugin; if even the
            # degraded payload cannot be written, drop this response and keep
            # serving rather than killing the process.
            continue
    return 0


if __name__ == '__main__':
    # Harness content can still surprise us; never let one character abort the
    # stream that carries every later response.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(serve())
