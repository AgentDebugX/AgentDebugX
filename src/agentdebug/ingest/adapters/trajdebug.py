"""Importer for TrajDebug / TRAJERRBENCH unified trajectory JSON.

TrajDebug (``THU-KEG/TrajDebug``, EMNLP 2026 Findings, MIT) distributes
TRAJERRBENCH as one JSON file per trajectory in a deliberately minimal shape::

    {
      "messages": [
        {"step": 0, "role": "user", "name": "human", "content": "..."},
        {"step": 1, "role": "assistant", "name": "Orchestrator", "content": "..."}
      ],
      "metadata": {
        "dataset": "alfworld",
        "task_id": "...",
        "task_description": "...",
        "reward": 0,
        "annotation": {"critical_error_step": 12,
                       "critical_error_type": "act.WrongTool"},
        "extra": {...}
      }
    }

Two properties of that format drive this importer.

**Step alignment is the point.** ``messages[i].step == i`` is a hard invariant of
their schema, and their scorer (``detector/score_steps.py``) reports accuracy in
terms of that index. Emitting one event per message with
``step_index == messages[i].step`` is therefore not a stylistic choice: it is
what allows a `Blame` produced by AgentDebugX and a `critical_error` produced by
TrajDebug to be scored against the same ground truth by the same script. The
round-trip is asserted in ``tests/test_ingest_trajdebug.py``.

**The format is already lossy, and we cannot un-lose it.** By the time a
trajectory reaches unified JSON, tool arguments, error fields, timing and event
types have all been flattened into one ``content`` string. This importer does
not attempt to re-derive them by parsing prose — a heuristic that recovered
``error`` from a substring search would silently manufacture signal for the
deterministic rule packs and make any benchmark comparison meaningless. Every
message becomes an event whose ``output`` is the raw string, and detectors that
depend on typed structure will legitimately find less here than they would on a
natively-ingested trace. That asymmetry is a property of the source data, and it
belongs in the benchmark write-up rather than being papered over here.

Ground-truth annotations are carried through on the trajectory metadata rather
than dropped, so an evaluation harness can read the label without re-opening the
source file. They are namespaced under ``trajdebug_*`` so nothing downstream
mistakes a benchmark label for a finding this library produced.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from agentdebug.schema import AgentEvent, AgentTrajectory

__all__ = [
    'convert_trajdebug_unified_payload',
    'looks_trajdebug_unified',
]

_VALID_ROLES = {'user', 'assistant', 'tool', 'system'}


def looks_trajdebug_unified(payload: Any) -> bool:
    """Return True when ``payload`` looks like a TrajDebug unified trajectory.

    Deliberately strict. A bare ``{"messages": [...]}` payload is already
    claimed by the generic ``messages`` importer, so this predicate keys on the
    combination that only TrajDebug produces: a ``metadata`` object carrying
    ``dataset`` plus a binary ``reward``, and messages that carry their own
    ``step`` index. Matching loosely here would hijack ordinary message exports.
    """

    if not isinstance(payload, dict):
        return False
    messages = payload.get('messages')
    metadata = payload.get('metadata')
    if not isinstance(messages, list) or not messages:
        return False
    if not isinstance(metadata, dict):
        return False
    if 'dataset' not in metadata or metadata.get('reward') not in (0, 1):
        return False
    first = messages[0]
    return (
        isinstance(first, dict)
        and isinstance(first.get('step'), int)
        and first.get('role') in _VALID_ROLES
    )


def convert_trajdebug_unified_payload(
    payload: Dict[str, Any],
    *,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal: Optional[str] = None,
    framework: Optional[str] = None,
) -> AgentTrajectory:
    """Convert one TrajDebug unified trajectory into an ``AgentTrajectory``."""

    # Imported here rather than at module scope: these are private helpers of
    # the importers module, and importing them at module scope would create a
    # cycle, since importers.py dispatches into this module.
    from agentdebug.ingest.adapters.importers import (
        ConversionError,
        _base_trajectory,
        _event_type_for_role,
        _module_for_event_type,
        _opt_str,
    )

    messages = payload.get('messages')
    if not isinstance(messages, Sequence) or not messages:
        raise ConversionError('trajdebug_unified payload needs a non-empty messages list')

    metadata = payload.get('metadata')
    if not isinstance(metadata, dict):
        raise ConversionError('trajdebug_unified payload needs a metadata object')

    dataset = _opt_str(metadata.get('dataset'))
    resolved_task_id = task_id or _opt_str(metadata.get('task_id'))
    annotation = metadata.get('annotation')
    annotation = annotation if isinstance(annotation, dict) else {}

    traj_metadata: Dict[str, Any] = {
        'source_format': 'trajdebug_unified',
        'trajdebug_dataset': dataset,
        'trajdebug_reward': metadata.get('reward'),
        # Ground truth, namespaced so it is never mistaken for our own output.
        'trajdebug_critical_error_step': annotation.get('critical_error_step'),
        'trajdebug_critical_error_type': annotation.get('critical_error_type'),
    }
    human_rationale = _opt_str(annotation.get('human_rationale'))
    if human_rationale:
        traj_metadata['trajdebug_human_rationale'] = human_rationale

    extra = metadata.get('extra')
    if isinstance(extra, dict):
        # Their per-dataset prose description of what the roles mean in this
        # format. Detectors that brief a model on the trace shape can use it;
        # keeping it means we do not have to re-derive it per dataset.
        framework_description = _opt_str(extra.get('agent_framework_description'))
        if framework_description:
            traj_metadata['agent_framework_description'] = framework_description

    traj = _base_trajectory(
        trace_id=trace_id or resolved_task_id,
        task_id=resolved_task_id,
        goal=goal or _opt_str(metadata.get('task_description')),
        framework=framework,
        fallback_framework=f'trajdebug/{dataset}' if dataset else 'trajdebug',
        metadata=traj_metadata,
    )

    seen_steps: List[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ConversionError(f'messages[{index}] is not an object')

        role = _opt_str(message.get('role')) or 'assistant'
        if role not in _VALID_ROLES:
            raise ConversionError(
                f'messages[{index}].role must be one of {sorted(_VALID_ROLES)}, got {role!r}'
            )

        step = message.get('step')
        if not isinstance(step, int):
            raise ConversionError(f'messages[{index}].step must be an int, got {step!r}')
        if step != index:
            # Their own validate_unified enforces this; a violation here means
            # the file was hand-edited, and silently renumbering would break the
            # index alignment that makes cross-system scoring possible.
            raise ConversionError(
                f'messages[{index}].step is {step}; TrajDebug requires step == index'
            )
        seen_steps.append(step)

        content = message.get('content')
        if not isinstance(content, str):
            raise ConversionError(f'messages[{index}].content must be a string')

        event_type = _event_type_for_role(role)
        speaker = _opt_str(message.get('name'))

        traj.add_event(
            AgentEvent(
                # Deterministic: the id is rendered into every judge prompt, so
                # a fresh UUID per import makes two runs over the same file send
                # different bytes -- unreproducible, and uncacheable.
                event_id=f'{traj.trace_id}:step{step}',
                trace_id=traj.trace_id,
                # `name` carries the original speaker label, which is the only
                # multi-agent signal the unified format preserves.
                agent_name=speaker or role,
                event_type=event_type,
                module=_module_for_event_type(event_type),
                step_index=step,
                output=content,
                metadata={
                    'source_format': 'trajdebug_unified',
                    'unified_role': role,
                    'unified_step': step,
                },
            )
        )

    if seen_steps != list(range(len(messages))):  # pragma: no cover - defensive
        raise ConversionError('step indices are not contiguous from zero')

    return traj
