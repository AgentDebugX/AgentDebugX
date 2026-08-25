"""JSON-lines bridge from DeepSeek Harness sessions to AgentDebugX.

This file belongs to the external DSH plugin. AgentDebugX itself stays
unmodified and is consumed only through its existing Python APIs.
"""

from __future__ import annotations

import json
import re
import sys
from collections import deque
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, get_args

from agentdebug.diagnose.pipeline import DiagnosePipeline
from agentdebug.ingest.adapters.importers import convert_directory, convert_file
from agentdebug.runtime.llm import CompletionResult, TokenUsage
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
            # A Harness turn holds many events under one step number, so
            # reusing that number leaves attribution unable to name a unique
            # decision point. Number the mapped events instead, matching the
            # convention of AgentDebugX's own importers; the Harness turn and
            # step stay in metadata.
            'step_index': len(normalized),
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


def _diagnose_deep(trajectory: AgentTrajectory, llm: Any) -> Any:
    """Run the DeepDebug profile, mirroring the CLI's ``--mode deep`` wiring.

    Deterministic findings are computed first and handed to DeepDebug as prior
    context, so the LLM tier refines the rule layer instead of replacing it.
    """

    from agentdebug.deep import DeepDebugAnalyzer
    from agentdebug.diagnose.context import DiagnoseContext
    from agentdebug.diagnose.detect.analyzers import HeuristicAnalyzer
    from agentdebug.diagnose.recover import suggest_from_context
    from agentdebug.recovery import DeepDebugRecovery

    detect_report = HeuristicAnalyzer().analyze(trajectory)
    # Memory retrieval stays off (the analyzer's default), which also keeps the
    # bridge from creating a deep-memory SQLite file beside the trace store.
    report = DeepDebugAnalyzer(
        llm=llm,
        prior_findings=detect_report.findings,
    ).analyze(trajectory).report
    report.metadata['upstream_detect'] = {
        'analyzer': detect_report.metadata.get('analyzer'),
        'summary': detect_report.summary,
        'finding_count': len(detect_report.findings),
    }
    context = DiagnoseContext.build(trajectory, report, None)
    proposals = suggest_from_context(DeepDebugRecovery(), context)
    report.suggestions = [proposal.suggestion_text for proposal in proposals]
    return report


def _diagnose_trajectory(
    trajectory: AgentTrajectory,
    *,
    store_path: str,
    dashboard_url: Optional[str],
    llm: Any = None,
) -> JsonObject:
    deep_error: Optional[str] = None
    deep_ran = False
    if llm is None:
        report = DiagnosePipeline.local_default().run(trajectory).report
    else:
        try:
            report = _diagnose_deep(trajectory, llm)
            deep_ran = True
        except Exception as exc:
            # DeepDebug refuses to answer when it cannot ground a root cause in
            # a real event, and the host model can be unavailable. Neither is a
            # reason to throw away the deterministic result the caller would
            # otherwise have received.
            host_error = getattr(llm, 'first_error', None)
            deep_error = (
                f'host model call failed: {host_error}' if host_error
                else f'{type(exc).__name__}: {_safe_text(str(exc))}'
            )
        if getattr(llm, 'completed', 0) == 0:
            # DeepDebug's tiers absorb failed calls and can still assemble a
            # verdict from their fallbacks. A verdict no model ever saw must
            # not be presented as a model-backed diagnosis.
            deep_ran = False
            deep_error = (
                f'no host model call succeeded: {llm.first_error}'
                if getattr(llm, 'first_error', None)
                else 'no host model call succeeded'
            )
        elif deep_ran and getattr(llm, 'first_error', None):
            deep_error = f'some host model calls failed: {llm.first_error}'
        if not deep_ran:
            report = DiagnosePipeline.local_default().run(trajectory).report
    store = _store(store_path)
    store.save_trajectory(trajectory)
    store.save_report(report)
    summary = _summary(report, dashboard_url)
    summary['mode'] = 'deep' if deep_ran else 'heuristic'
    if deep_error is not None:
        summary['deepError'] = deep_error
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


class HostLLMClient:
    """``LLMClient`` that borrows the model the Harness host already runs.

    AgentDebugX accepts any object satisfying the ``LLMClient`` protocol, so the
    LLM tiers can be driven by the host's configured model instead of asking the
    user to provision a second API key. Completions travel back over the same
    JSON-lines pipe as a reverse request.
    """

    def __init__(self, protocol: '_Protocol', token: str, model: str) -> None:
        self._protocol = protocol
        self._token = token
        self.model = model
        #: First transport failure, kept because AgentDebugX's LLM tiers absorb
        #: individual call errors and would otherwise report a lost model call
        #: as an inconclusive diagnosis.
        self.first_error: Optional[str] = None
        #: Completed calls, so a run that never reached the model cannot be
        #: presented as a model-backed diagnosis.
        self.completed = 0

    def complete(
        self,
        messages: List[Dict[str, Any]],
        *,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> CompletionResult:
        try:
            result = self._protocol.call(
                'llm.complete',
                {
                    'token': self._token,
                    'messages': _sanitize(messages),
                    'responseFormat': response_format,
                    'temperature': temperature,
                    'maxTokens': max_tokens,
                    'timeoutMs': int(timeout * 1000),
                },
            )
        except Exception as exc:
            if self.first_error is None:
                self.first_error = _safe_text(str(exc))
            raise
        if not isinstance(result, dict):
            if self.first_error is None:
                self.first_error = 'host llm returned a malformed completion'
            raise RuntimeError('host llm returned a malformed completion')
        self.completed += 1
        usage = result.get('usage') or {}
        return CompletionResult(
            text=str(result.get('text') or ''),
            raw=result,
            usage=TokenUsage(
                prompt_tokens=int(usage.get('promptTokens') or 0),
                completion_tokens=int(usage.get('completionTokens') or 0),
                calls=1,
            ),
        )


def _host_llm(protocol: Optional['_Protocol'], params: JsonObject) -> Any:
    """Build the host-backed client the requested mode needs, if any."""

    mode = str(params.get('mode') or 'heuristic')
    if mode == 'heuristic':
        return None
    if mode != 'deep':
        raise ValueError(
            f'unsupported bridge mode {mode!r}; supported modes are heuristic and deep'
        )
    if protocol is None:
        raise ValueError('deep mode requires the bidirectional bridge protocol')
    llm = params.get('llm')
    if not isinstance(llm, dict) or not llm.get('token'):
        raise ValueError('deep mode requires an llm token issued by the host')
    return HostLLMClient(protocol, str(llm['token']), str(llm.get('model') or 'host'))


def handle(method: str, params: JsonObject, protocol: Optional['_Protocol'] = None) -> Any:
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
        llm = _host_llm(protocol, params)
        trajectory = session_to_trajectory(params['session'])
        return _diagnose_trajectory(
            trajectory,
            store_path=str(params['store']),
            dashboard_url=params.get('dashboardUrl'),
            llm=llm,
        )

    if method == 'diagnose_path':
        llm = _host_llm(protocol, params)
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
            llm=llm,
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


class _MalformedLine(Exception):
    """One unreadable input line, reported without ending the stream."""


class _Protocol:
    """Bidirectional JSON-lines channel over the plugin's stdio pipe.

    The host drives the bridge, but LLM-backed diagnosis needs to call back into
    the host mid-request, so messages travel both ways over the one pipe. A
    message carrying ``method`` is a request; anything else answers a reverse
    call this side is waiting on.
    """

    def __init__(self, lines: Iterable[str], out: TextIO) -> None:
        self._lines = iter(lines)
        self._out = out
        self._deferred: 'deque[JsonObject]' = deque()
        self._sequence = 0

    def write(self, payload: JsonObject) -> None:
        try:
            self._out.write(_encode_response(payload))
            self._out.flush()
        except Exception:
            # stdout is the only channel back to the plugin; if even the
            # degraded payload cannot be written, drop this message and keep
            # serving rather than killing the process.
            pass

    def _read(self) -> Optional[JsonObject]:
        """Return the next parsed message, or None once input ends."""

        for line in self._lines:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except Exception as exc:
                raise _MalformedLine(str(exc)) from exc
            if not isinstance(message, dict):
                raise _MalformedLine('expected a JSON object')
            return message
        return None

    def next_request(self) -> Optional[JsonObject]:
        if self._deferred:
            return self._deferred.popleft()
        while True:
            message = self._read()
            if message is None:
                return None
            if message.get('method') is not None:
                return message
            # A reply with nobody waiting for it: no in-flight reverse call can
            # own it, so drop it rather than stalling the loop.

    def call(self, method: str, params: JsonObject, *, depth: int = 0) -> Any:
        """Issue a reverse request and wait for its reply.

        Host requests that arrive while waiting are served inline instead of
        being queued, so a long LLM-backed run does not stall the automatic
        per-turn capture behind it.
        """

        self._sequence += 1
        call_id = f'llm-{self._sequence}'
        self.write({'id': call_id, 'method': method, 'params': params})
        while True:
            try:
                message = self._read()
            except _MalformedLine as exc:
                self.write({
                    'id': None,
                    'error': {'type': 'MalformedLine', 'message': _safe_text(str(exc))},
                })
                continue
            if message is None:
                raise RuntimeError(f'bridge input closed while awaiting {method}')
            if message.get('method') is not None:
                if depth >= 8:
                    self._deferred.append(message)
                else:
                    self.write(_respond(self, message, depth=depth + 1))
                continue
            if message.get('id') != call_id:
                continue
            error = message.get('error')
            if error:
                raise RuntimeError(
                    f"{error.get('type') or 'HostError'}: {error.get('message') or 'unknown error'}"
                )
            return message.get('result')


def _respond(protocol: _Protocol, request: JsonObject, *, depth: int = 0) -> JsonObject:
    try:
        params = request.get('params')
        if not isinstance(params, dict):
            params = {}
        result = handle(str(request.get('method') or ''), params, protocol)
        return {'id': request.get('id'), 'result': result}
    except Exception as exc:  # protocol boundary: convert every failure
        return {
            'id': request.get('id'),
            'error': {'type': type(exc).__name__, 'message': _safe_text(str(exc))},
        }


def serve(
    lines: Optional[Iterable[str]] = None,
    out: Optional[TextIO] = None,
) -> int:
    # Resolved at call time so a caller can swap either stream.
    protocol = _Protocol(sys.stdin if lines is None else lines, out or sys.stdout)
    while True:
        try:
            request = protocol.next_request()
        except _MalformedLine as exc:
            protocol.write({
                'id': None,
                'error': {'type': 'MalformedLine', 'message': _safe_text(str(exc))},
            })
            continue
        if request is None:
            return 0
        protocol.write(_respond(protocol, request))


if __name__ == '__main__':
    # The caller always writes UTF-8, but Python defaults stdio to the system
    # locale encoding, which is a legacy codepage on many Windows installs
    # (GBK here). Decoding UTF-8 request bytes as GBK corrupts the JSON at the
    # first non-ASCII character, so pin both directions instead of inheriting.
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    # Harness content can still surprise us; never let one character abort the
    # stream that carries every later response.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(serve())
