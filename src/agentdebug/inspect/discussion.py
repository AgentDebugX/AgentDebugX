"""Provider-neutral discussion over pinned trajectory and report snapshots.

The service in this module is deliberately core-safe.  It knows about the
portable AgentDebugX schema and the runtime LLM protocol, but imports no web UI
or provider SDK.  A callable or client can be injected by applications and
tests.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from agentdebug.runtime.llm import CompletionResult, TokenUsage
from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    model_to_dict,
    report_from_json,
    trajectory_from_json,
)

JsonObject = Dict[str, Any]
LLMCallable = Callable[..., Any]

_MAX_TOOL_ROUNDS = 4
_DEFAULT_RANGE_LIMIT = 25
_MAX_RANGE_LIMIT = 100
_MAX_VALUE_CHARS = 1200


class DiscussionError(Exception):
    """Base error with a stable code and sanitized route-facing message."""

    code = 'discussion_error'
    default_message = 'The discussion request could not be completed.'

    def __init__(self, message: Optional[str] = None) -> None:
        self.public_message = message or self.default_message
        super().__init__(self.public_message)


class DiscussionValidationError(DiscussionError):
    code = 'invalid_discussion_request'
    default_message = 'The discussion request is invalid.'


class UnknownEventError(DiscussionError):
    code = 'unknown_event'
    default_message = 'The requested event does not exist in this snapshot.'


class ToolBoundsError(DiscussionError):
    code = 'tool_bounds'
    default_message = 'The requested event range is outside the snapshot bounds.'


class InvalidCitationError(DiscussionError):
    code = 'invalid_citation'
    default_message = 'The response cites an event outside this snapshot.'


class DiscussionLLMError(DiscussionError):
    code = 'llm_unavailable'
    default_message = 'The discussion model could not complete the request.'


class InvalidDiscussionResponseError(DiscussionError):
    code = 'invalid_llm_response'
    default_message = 'The discussion model returned an invalid response.'


@dataclass(frozen=True)
class EventCitation:
    """A validated reference to one event in the pinned trajectory."""

    event_id: str
    quote: Optional[str] = None


@dataclass(frozen=True)
class ReportRevisionDraft:
    """Unapplied, structured edits proposed for the pinned report."""

    base_report_id: str
    base_report_digest: str
    changes: JsonObject
    rationale: Optional[str] = None
    citations: List[EventCitation] = field(default_factory=list)

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class DiscussionResult:
    """Safe result returned by :meth:`DiscussionService.discuss`."""

    content: str
    citations: List[EventCitation] = field(default_factory=list)
    revision_draft: Optional[ReportRevisionDraft] = None
    usage: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        return asdict(self)


def snapshot_digest(
    trajectory: AgentTrajectory,
    report: DiagnosticReport,
) -> str:
    """Return a deterministic digest for a trajectory/report pair."""

    payload = {
        'trajectory': model_to_dict(trajectory),
        'report': model_to_dict(report),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


class DiscussionService:
    """Discuss one immutable trajectory and diagnostic-report snapshot."""

    def __init__(
        self,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
        llm: Optional[Any] = None,
        *,
        model: Optional[str] = None,
        max_context_events: int = 20,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
    ) -> None:
        if trajectory.trace_id != report.trace_id:
            raise DiscussionValidationError(
                'The trajectory and report must refer to the same trace.'
            )
        if max_context_events < 0:
            raise DiscussionValidationError(
                'max_context_events must be zero or greater.'
            )
        if max_tool_rounds < 1:
            raise DiscussionValidationError('max_tool_rounds must be positive.')

        # JSON round-tripping pins values and prevents later caller mutation.
        self._trajectory = trajectory_from_json(
            json.dumps(model_to_dict(trajectory), ensure_ascii=False)
        )
        self._report = report_from_json(
            json.dumps(model_to_dict(report), ensure_ascii=False)
        )
        self._events = list(self._trajectory.events)
        self._events_by_id: Dict[str, AgentEvent] = {}
        for event in self._events:
            if event.event_id in self._events_by_id:
                raise DiscussionValidationError(
                    'Event IDs must be unique within a trajectory snapshot.'
                )
            self._events_by_id[event.event_id] = event

        self.llm = llm
        self.model = model or str(getattr(llm, 'model', '') or 'unspecified')
        self.max_context_events = max_context_events
        self.max_tool_rounds = max_tool_rounds
        self.digest = snapshot_digest(self._trajectory, self._report)
        self.report_digest = _digest_json(model_to_dict(self._report))

    @property
    def trajectory_snapshot(self) -> AgentTrajectory:
        """Return a detached copy of the pinned trajectory."""

        return trajectory_from_json(
            json.dumps(model_to_dict(self._trajectory), ensure_ascii=False)
        )

    @property
    def report_snapshot(self) -> DiagnosticReport:
        """Return a detached copy of the pinned report."""

        return report_from_json(
            json.dumps(model_to_dict(self._report), ensure_ascii=False)
        )

    def build_context(self) -> JsonObject:
        """Build compact, JSON-safe context without embedding full artifacts."""

        report = self._report
        findings = [
            {
                'finding_id': finding.finding_id,
                'event_id': finding.event_id,
                'step_index': finding.step_index,
                'failure_mode': finding.failure_mode.name,
                'evidence': [_compact_value(item) for item in finding.evidence[:3]],
                'suggestion': _compact_value(finding.suggestion),
            }
            for finding in report.findings[:10]
        ]
        event_summaries = [
            _event_summary(event, position)
            for position, event in enumerate(
                self._events[: self.max_context_events]
            )
        ]
        return {
            'snapshot_digest': self.digest,
            'trace': {
                'trace_id': self._trajectory.trace_id,
                'task_id': self._trajectory.task_id,
                'goal': _compact_value(self._trajectory.goal),
                'framework': self._trajectory.framework,
                'event_count': len(self._events),
                'events': event_summaries,
                'events_truncated': len(self._events) > len(event_summaries),
            },
            'report': {
                'report_id': report.report_id,
                'summary': _compact_value(report.summary),
                'root_cause_event_id': report.root_cause_event_id,
                'root_cause_step_index': report.root_cause_step_index,
                'findings': findings,
                'suggestions': [
                    _compact_value(item) for item in report.suggestions[:10]
                ],
            },
        }

    def get_event_details(self, event_id: str) -> JsonObject:
        """Return one event by stable ID, independent of duplicate step indexes."""

        event = self._events_by_id.get(str(event_id))
        if event is None:
            raise UnknownEventError()
        return _safe_event_details(event)

    def get_event_range(
        self,
        start: int,
        end: int,
        *,
        limit: int = _DEFAULT_RANGE_LIMIT,
    ) -> List[JsonObject]:
        """Return events at inclusive zero-based snapshot positions."""

        if limit < 1 or limit > _MAX_RANGE_LIMIT:
            raise ToolBoundsError(
                f'limit must be between 1 and {_MAX_RANGE_LIMIT}.'
            )
        if start < 0 or end < start or end >= len(self._events):
            raise ToolBoundsError()
        if end - start + 1 > limit:
            raise ToolBoundsError('The requested event range exceeds the limit.')
        return [
            {
                'position': position,
                **_safe_event_details(self._events[position]),
            }
            for position in range(start, end + 1)
        ]

    def get_report_details(self) -> JsonObject:
        """Return the pinned report; callers cannot mutate the service snapshot."""

        return copy.deepcopy(model_to_dict(self._report))

    def call_tool(
        self,
        name: str,
        arguments: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Dispatch one of the three read-only discussion tools."""

        args = dict(arguments or {})
        if name == 'get_event_details':
            return self.get_event_details(str(args.get('event_id', '')))
        if name == 'get_event_range':
            try:
                start = int(args['start'])
                end = int(args['end'])
                limit = int(args.get('limit', _DEFAULT_RANGE_LIMIT))
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolBoundsError() from exc
            return self.get_event_range(start, end, limit=limit)
        if name == 'get_report_details':
            return self.get_report_details()
        raise DiscussionValidationError('Unknown discussion tool.')

    def validate_citations(
        self,
        citations: Optional[Iterable[Any]],
    ) -> List[EventCitation]:
        """Normalize citations and reject unknown event IDs."""

        validated: List[EventCitation] = []
        seen = set()
        for item in citations or []:
            if isinstance(item, EventCitation):
                event_id, quote = item.event_id, item.quote
            elif isinstance(item, str):
                event_id, quote = item, None
            elif isinstance(item, Mapping):
                event_id = str(item.get('event_id') or '')
                quote_value = item.get('quote')
                quote = str(quote_value) if quote_value is not None else None
            else:
                raise InvalidCitationError()
            if event_id not in self._events_by_id:
                raise InvalidCitationError()
            if event_id not in seen:
                validated.append(
                    EventCitation(
                        event_id=event_id,
                        quote=_compact_value(quote) if quote else None,
                    )
                )
                seen.add(event_id)
        return validated

    def parse_response(
        self,
        response: Any,
        *,
        usage: Optional[Any] = None,
    ) -> DiscussionResult:
        """Parse text or a JSON envelope into a validated discussion result."""

        payload = _response_payload(response)
        if payload is None:
            content = _response_text(response).strip()
            if not content:
                raise InvalidDiscussionResponseError()
            return DiscussionResult(content=content, usage=_safe_usage(usage))

        content = str(
            payload.get('content')
            or payload.get('answer')
            or payload.get('message')
            or ''
        ).strip()
        if not content:
            raise InvalidDiscussionResponseError()
        citations = self.validate_citations(payload.get('citations'))
        raw_draft = (
            payload.get('report_revision')
            or payload.get('revision_draft')
            or payload.get('proposal')
        )
        draft = self._parse_revision_draft(raw_draft, citations)
        return DiscussionResult(
            content=content,
            citations=citations,
            revision_draft=draft,
            usage=_safe_usage(usage or payload.get('usage')),
        )

    def discuss(
        self,
        user_message: str,
        *,
        history: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> DiscussionResult:
        """Run one model turn, allowing only read-only snapshot tools."""

        if self.llm is None:
            raise DiscussionValidationError('An LLM client or callable is required.')
        if not str(user_message).strip():
            raise DiscussionValidationError('The discussion message cannot be empty.')

        messages: List[JsonObject] = [
            {'role': 'system', 'content': _system_prompt(self.build_context())}
        ]
        for message in history or []:
            role = str(message.get('role') or '')
            if role not in {'user', 'assistant'}:
                continue
            messages.append(
                {'role': role, 'content': str(message.get('content') or '')}
            )
        messages.append({'role': 'user', 'content': str(user_message)})

        try:
            if hasattr(self.llm, 'chat') and callable(self.llm.chat):
                return self._discuss_with_chat(messages)
            response = _invoke_completion(self.llm, messages, DISCUSSION_TOOLS)
            usage = getattr(response, 'usage', None)
            return self.parse_response(response, usage=usage)
        except DiscussionError:
            raise
        except Exception as exc:
            # Never surface provider payloads, credentials, or request internals.
            raise DiscussionLLMError() from exc

    def _discuss_with_chat(self, messages: List[JsonObject]) -> DiscussionResult:
        usage_before = _usage_dict(getattr(self.llm, 'usage_total', None))
        for _ in range(self.max_tool_rounds):
            choice = self.llm.chat(
                messages,
                tools=DISCUSSION_TOOLS,
                tool_choice='auto',
                temperature=0.0,
            )
            message = dict(choice.get('message') or {})
            tool_calls = message.get('tool_calls') or []
            if not tool_calls:
                usage_after = _usage_dict(getattr(self.llm, 'usage_total', None))
                usage = _usage_delta(usage_before, usage_after)
                return self.parse_response(message.get('content') or '', usage=usage)

            messages.append(
                {
                    'role': 'assistant',
                    'content': message.get('content') or '',
                    'tool_calls': tool_calls,
                }
            )
            for call in tool_calls:
                function = call.get('function') or {}
                arguments = _json_object(function.get('arguments')) or {}
                try:
                    result = self.call_tool(str(function.get('name') or ''), arguments)
                    tool_content = json.dumps(result, ensure_ascii=False)
                except DiscussionError as exc:
                    tool_content = json.dumps(
                        {'error': exc.code, 'message': exc.public_message}
                    )
                messages.append(
                    {
                        'role': 'tool',
                        'tool_call_id': str(call.get('id') or ''),
                        'content': tool_content,
                    }
                )
        raise InvalidDiscussionResponseError(
            'The discussion model exceeded the tool-call limit.'
        )

    def _parse_revision_draft(
        self,
        raw: Any,
        response_citations: List[EventCitation],
    ) -> Optional[ReportRevisionDraft]:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise InvalidDiscussionResponseError()
        supplied_id = raw.get('base_report_id')
        if supplied_id is not None and str(supplied_id) != self._report.report_id:
            raise InvalidDiscussionResponseError()
        changes_value = raw.get('changes')
        if changes_value is None:
            changes_value = {
                key: value
                for key, value in raw.items()
                if key
                not in {
                    'base_report_id',
                    'base_report_digest',
                    'rationale',
                    'citations',
                }
            }
        if not isinstance(changes_value, Mapping):
            raise InvalidDiscussionResponseError()
        changes = _sanitize_revision_changes(dict(changes_value))
        self._validate_revision_event_ids(changes)
        draft_citations = self.validate_citations(
            raw.get('citations', response_citations)
        )
        rationale = raw.get('rationale')
        return ReportRevisionDraft(
            base_report_id=self._report.report_id,
            base_report_digest=self.report_digest,
            changes=changes,
            rationale=_compact_value(rationale) if rationale is not None else None,
            citations=draft_citations,
        )

    def _validate_revision_event_ids(self, changes: JsonObject) -> None:
        event_ids: List[str] = []
        root_event_id = changes.get('root_cause_event_id')
        if root_event_id is not None:
            event_ids.append(str(root_event_id))
        findings = changes.get('findings')
        if isinstance(findings, list):
            for finding in findings:
                if isinstance(finding, Mapping) and finding.get('event_id') is not None:
                    event_ids.append(str(finding['event_id']))
        if any(event_id not in self._events_by_id for event_id in event_ids):
            raise InvalidCitationError()


DISCUSSION_TOOLS: List[JsonObject] = [
    {
        'type': 'function',
        'function': {
            'name': 'get_event_details',
            'description': 'Read one event from the pinned trace by event_id.',
            'parameters': {
                'type': 'object',
                'properties': {'event_id': {'type': 'string'}},
                'required': ['event_id'],
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_event_range',
            'description': (
                'Read an inclusive range using zero-based snapshot positions.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'integer', 'minimum': 0},
                    'end': {'type': 'integer', 'minimum': 0},
                    'limit': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': _MAX_RANGE_LIMIT,
                    },
                },
                'required': ['start', 'end'],
                'additionalProperties': False,
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_report_details',
            'description': 'Read the pinned diagnostic report snapshot.',
            'parameters': {
                'type': 'object',
                'properties': {},
                'additionalProperties': False,
            },
        },
    },
]


def _event_summary(event: AgentEvent, position: int) -> JsonObject:
    return {
        'position': position,
        'event_id': event.event_id,
        'step_index': event.step_index,
        'event_type': str(event.event_type),
        'agent_name': event.agent_name,
        'module': event.module,
        'input': _compact_value(event.input),
        'output': _compact_value(event.output),
        'error': _compact_value(event.error),
    }


def _safe_event_details(event: AgentEvent) -> JsonObject:
    """Serialize event evidence without exposing local artifact paths."""

    payload = copy.deepcopy(model_to_dict(event))
    safe_artifacts = []
    for artifact in payload.get('artifacts') or []:
        if not isinstance(artifact, dict):
            continue
        safe_artifacts.append({
            'modality': artifact.get('modality'),
            'media_type': artifact.get('media_type'),
            'description': artifact.get('description'),
            'metadata': {
                key: value
                for key, value in (artifact.get('metadata') or {}).items()
                if str(key).lower() not in {'path', 'source_dir', 'uri'}
            },
        })
    payload['artifacts'] = safe_artifacts
    return payload


def _compact_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE_CHARS else value[:_MAX_VALUE_CHARS] + '…'
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= _MAX_VALUE_CHARS else text[:_MAX_VALUE_CHARS] + '…'


def _digest_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _system_prompt(context: JsonObject) -> str:
    return (
        'You are discussing an AgentDebugX diagnostic snapshot. Use only the '
        'provided snapshot and read-only tools. Cite evidence by exact event_id. '
        'Do not claim that duplicate step_index values identify an event. Return '
        'plain text or a JSON object with content, citations, and an optional '
        'report_revision containing changes, rationale, and citations. A revision '
        'is only a draft and must never be described as applied.\n\n'
        + json.dumps(context, ensure_ascii=False, separators=(',', ':'))
    )


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, CompletionResult):
        return response.text
    if isinstance(response, Mapping):
        if 'text' in response:
            return str(response.get('text') or '')
        if 'content' in response and isinstance(response.get('content'), str):
            return str(response.get('content') or '')
    return str(getattr(response, 'text', '') or '')


def _response_payload(response: Any) -> Optional[JsonObject]:
    if isinstance(response, Mapping) and any(
        key in response
        for key in ('answer', 'message', 'citations', 'report_revision', 'proposal')
    ):
        return dict(response)
    return _json_object(_response_text(response))


def _json_object(value: Any) -> Optional[JsonObject]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith('```'):
        first_newline = text.find('\n')
        text = text[first_newline + 1 :] if first_newline >= 0 else text[3:]
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _safe_usage(value: Any) -> JsonObject:
    usage = _usage_dict(value)
    return {
        key: usage[key]
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'calls', 'cost_usd')
        if key in usage
    }


def _usage_dict(value: Any) -> JsonObject:
    if isinstance(value, TokenUsage):
        return {
            'prompt_tokens': value.prompt_tokens,
            'completion_tokens': value.completion_tokens,
            'total_tokens': value.total_tokens,
            'calls': value.calls,
            'cost_usd': value.cost_usd,
        }
    if isinstance(value, Mapping):
        out: JsonObject = {}
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'calls', 'cost_usd'):
            number = value.get(key)
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                out[key] = number
        return out
    return {}


def _usage_delta(before: JsonObject, after: JsonObject) -> JsonObject:
    if not after:
        return {}
    delta: JsonObject = {}
    for key, value in after.items():
        prior = before.get(key, 0)
        if isinstance(value, (int, float)) and isinstance(prior, (int, float)):
            delta[key] = value - prior
    return delta


def _invoke_completion(
    llm: Any,
    messages: List[JsonObject],
    tools: List[JsonObject],
) -> Any:
    target = llm.complete if hasattr(llm, 'complete') and callable(llm.complete) else llm
    if not callable(target):
        raise DiscussionValidationError('The injected LLM is not callable.')
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(messages)
    parameters = signature.parameters
    kwargs: JsonObject = {}
    if 'messages' in parameters:
        kwargs['messages'] = messages
    if 'tools' in parameters:
        kwargs['tools'] = tools
    if 'model' in parameters:
        kwargs['model'] = getattr(llm, 'model', None)
    if kwargs:
        return target(**kwargs)
    return target(messages)


def _sanitize_revision_changes(changes: JsonObject) -> JsonObject:
    allowed = {
        'summary',
        'root_cause_event_id',
        'root_cause_agent',
        'root_cause_step_index',
        'findings',
        'suggestions',
        'attribution',
        'recovery',
        'metadata',
    }
    unknown = set(changes) - allowed
    if unknown:
        raise InvalidDiscussionResponseError()
    try:
        # A JSON round trip also strips custom objects from persisted proposals.
        return json.loads(json.dumps(changes, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise InvalidDiscussionResponseError() from exc


__all__ = [
    'DISCUSSION_TOOLS',
    'DiscussionError',
    'DiscussionLLMError',
    'DiscussionResult',
    'DiscussionService',
    'DiscussionValidationError',
    'EventCitation',
    'InvalidCitationError',
    'InvalidDiscussionResponseError',
    'ReportRevisionDraft',
    'ToolBoundsError',
    'UnknownEventError',
    'snapshot_digest',
]
