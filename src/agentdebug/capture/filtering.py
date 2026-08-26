"""Capture-only filtering, bounding, and redaction."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agentdebug.capture.contracts import PreparedTrajectory
from agentdebug.hub.scrub import SCRUBBER_VERSION, Scrubber
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType

MAX_FIELD_CHARS = 65_536
MAX_TRAJECTORY_CHARS = 2_000_000


def prepare_for_capture(trajectory: AgentTrajectory) -> PreparedTrajectory:
    copier = getattr(trajectory, 'model_copy', None)
    prepared = copier(deep=True) if callable(copier) else trajectory.copy(deep=True)
    counters: Dict[str, int] = {}
    original_by_id = {event.event_id: event for event in prepared.events}
    excluded = {
        event.event_id: rule
        for event in prepared.events
        if (rule := _excluded_by(event)) is not None
    }
    for event in prepared.events:
        if (
            event.event_type == EventType.TOOL_RESULT.value
            and event.parent_event_id in excluded
        ):
            excluded[event.event_id] = 'integration_owned'
    kept: List[AgentEvent] = []
    for event in prepared.events:
        rule = excluded.get(event.event_id)
        if rule is not None:
            counters[rule] = counters.get(rule, 0) + 1
            continue
        kept.append(event)
    kept_ids = {event.event_id for event in kept}
    for event in kept:
        event.parent_event_id = _nearest_kept_parent(
            event.parent_event_id, original_by_id, kept_ids
        )
    prepared.events = kept

    total = 0
    bounded: List[AgentEvent] = []
    for event in prepared.events:
        _bound_event(event, counters)
        size = len(json.dumps(_event_payload(event), default=str))
        if total + size > MAX_TRAJECTORY_CHARS:
            counters['trajectory_truncated'] = (
                counters.get('trajectory_truncated', 0) + 1
            )
            break
        total += size
        bounded.append(event)
    prepared.events = bounded
    for index, event in enumerate(prepared.events):
        event.step_index = index

    report = Scrubber().scrub_trajectory(prepared)
    for name, count in report.replacements.items():
        counters[f'redacted.{name}'] = count
    prepared.metadata['capture_filter_counts'] = dict(counters)
    prepared.metadata['capture_scrubber_version'] = SCRUBBER_VERSION
    return PreparedTrajectory(trajectory=prepared, counters=counters)


def _excluded_by(event: AgentEvent) -> Optional[str]:
    if event.event_type == EventType.REFLECTION.value or event.module == 'reasoning':
        return 'private_reasoning'
    metadata = event.metadata
    if metadata.get('agentdebug_capture_owned') is True:
        return 'integration_owned'
    if metadata.get('claude_code_origin_kind') == 'agentdebug_capture':
        return 'integration_owned'
    if metadata.get('claude_code_prompt_source') == 'agentdebug-capture':
        return 'integration_owned'
    if metadata.get('codex_origin') == 'agentdebug_capture':
        return 'integration_owned'
    if event.event_type == EventType.TOOL_CALL.value and _managed_skill_path(
        event.input
    ):
        return 'integration_owned'
    return None


def _managed_skill_path(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ('path', 'file_path', 'uri'):
        candidate = value.get(key)
        if not isinstance(candidate, str):
            continue
        normalized = candidate.replace('\\', '/')
        if any(
            marker in normalized
            for marker in (
                '/.claude/skills/agentdebug/',
                '/.agents/skills/agentdebug/',
                '/.codex/skills/agentdebug/',
            )
        ):
            return True
    return False


def _nearest_kept_parent(
    parent_id: Optional[str],
    original_by_id: Dict[str, AgentEvent],
    kept_ids: set[str],
) -> Optional[str]:
    seen: set[str] = set()
    current = parent_id
    while current is not None and current not in kept_ids and current not in seen:
        seen.add(current)
        parent = original_by_id.get(current)
        current = None if parent is None else parent.parent_event_id
    return current if current in kept_ids else None


def _bound_event(event: AgentEvent, counters: Dict[str, int]) -> None:
    event.input = _bound_value(event.input, counters)
    event.output = _bound_value(event.output, counters)
    event.error = _bound_value(event.error, counters)
    event.metadata = _bound_value(event.metadata, counters)


def _bound_value(value: Any, counters: Dict[str, int]) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        counters['payload_truncated'] = counters.get('payload_truncated', 0) + 1
        return value[:MAX_FIELD_CHARS] + '<truncated>'
    if isinstance(value, dict):
        return {key: _bound_value(item, counters) for key, item in value.items()}
    if isinstance(value, list):
        return [_bound_value(item, counters) for item in value]
    if isinstance(value, tuple):
        return tuple(_bound_value(item, counters) for item in value)
    return value


def _event_payload(event: AgentEvent) -> Dict[str, Any]:
    dumper = getattr(event, 'model_dump', None)
    return dumper(mode='json') if callable(dumper) else event.dict()
