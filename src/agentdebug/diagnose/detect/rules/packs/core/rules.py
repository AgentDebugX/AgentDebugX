"""Default analysis rules loaded for every heuristic analysis.

The core pack intentionally contains both single-event rules and cross-event
trajectory rules. ``HeuristicAnalyzer.analyze()`` loads them together so users
configure one concept, a rule pack, instead of separately choosing
"rule-based" vs. "detectors".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    EventType,
    FailureFinding,
    FailureMode,
    new_id,
)
from agentdebug.diagnose.detect.rules.base import RuleMatch
from agentdebug.schema import SEED_FAILURE_MODES


@dataclass(frozen=True)
class KeywordRule:
    rule_id: str
    mode_id: str
    needles: List[str]
    evidence: str
    confidence_basis: str
    priority: int
    rule_pack: str = 'core'

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        lowered = text.lower()
        if not _contains(lowered, self.needles):
            return None
        return RuleMatch(
            mode_id=self.mode_id,
            evidence=[self.evidence],
            rule_id=self.rule_id,
            rule_pack=self.rule_pack,
            confidence_basis=self.confidence_basis,
            priority=self.priority,
        )


class EventErrorRule:
    rule_id = 'core.system.event_error'
    rule_pack = 'core'
    priority = 100

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        if not event.error:
            return None
        if _event_type_value(event.event_type) in {
            EventType.TOOL_CALL.value,
            EventType.TOOL_RESULT.value,
        }:
            return RuleMatch(
                mode_id='system.tool_execution_error',
                evidence=[event.error],
                rule_id='core.system.tool_execution_error.explicit_error',
                rule_pack=self.rule_pack,
                confidence_basis='explicit error field on tool event',
                priority=self.priority,
            )
        return RuleMatch(
            mode_id='system.environment_error',
            evidence=[event.error],
            rule_id='core.system.environment_error.explicit_error',
            rule_pack=self.rule_pack,
            confidence_basis='explicit error field on non-tool event',
            priority=self.priority,
        )


class MetadataStatusRule:
    rule_id = 'core.system.environment_error.metadata_status'
    rule_pack = 'core'
    priority = 1000

    def match(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
    ) -> Optional[RuleMatch]:
        status = str(event.metadata.get('status', '')).lower()
        if status not in {'failed', 'failure', 'error'}:
            return None
        return RuleMatch(
            mode_id='system.environment_error',
            evidence=['metadata status marks event as failed'],
            rule_id=self.rule_id,
            rule_pack=self.rule_pack,
            confidence_basis='metadata status is failed/failure/error',
            priority=self.priority,
        )


@dataclass(frozen=True)
class RepeatedToolCallRule:
    """Flag a tool called with identical args across multiple events."""

    threshold: int = 3
    rule_id: str = 'core.trajectory.repeated_tool_call'
    rule_pack: str = 'core'
    priority: int = 100
    source: str = 'repeated_tool_call'

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        counts: dict[tuple[str, str], List[AgentEvent]] = {}
        for event in trajectory.events:
            if _event_type_value(event.event_type) != EventType.TOOL_CALL.value:
                continue
            key = (event.agent_name, _normalize(event.input))
            counts.setdefault(key, []).append(event)

        findings: List[FailureFinding] = []
        mode = SEED_FAILURE_MODES['planning.inefficient_plan']
        for (agent, signature), events in counts.items():
            if len(events) < self.threshold:
                continue
            target = events[-1]
            findings.append(
                _finding(
                    mode=mode,
                    event=target,
                    evidence=[
                        f'{len(events)} identical TOOL_CALL events with '
                        f'signature={_truncate(signature, 80)} on agent={agent}',
                    ],
                    metadata={
                        'source': self.source,
                        'detected_count': len(events),
                    },
                    rule=self,
                    confidence_basis=(
                        f'identical tool-call signature repeated {len(events)} times; '
                        f'threshold={self.threshold}'
                    ),
                )
            )
        return findings


@dataclass(frozen=True)
class RepeatedStateRule:
    """Flag a sliding window where observations/outputs do not change."""

    window: int = 4
    threshold: int = 3
    rule_id: str = 'core.trajectory.repeated_state'
    rule_pack: str = 'core'
    priority: int = 110
    source: str = 'repeated_state'

    def __post_init__(self) -> None:
        if self.threshold > self.window:
            raise ValueError('threshold must be <= window')

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        events = [
            event for event in trajectory.events
            if _event_type_value(event.event_type) in {
                EventType.OBSERVATION.value,
                EventType.AGENT_STEP.value,
                EventType.PLAN.value,
                EventType.TOOL_RESULT.value,
            }
        ]
        if len(events) < self.window:
            return []

        findings: List[FailureFinding] = []
        mode = SEED_FAILURE_MODES['planning.inefficient_plan']
        flagged_event_ids: set[str] = set()
        for index in range(len(events) - self.window + 1):
            current_window = events[index : index + self.window]
            signatures = [_normalize(_state_signature(event)) for event in current_window]
            most_common, count = _mode_count(signatures)
            if count < self.threshold:
                continue
            target = current_window[-1]
            if target.event_id in flagged_event_ids:
                continue
            flagged_event_ids.add(target.event_id)
            findings.append(
                _finding(
                    mode=mode,
                    event=target,
                    evidence=[
                        f'state repeated {count}x within window of '
                        f'{self.window} events; signature={_truncate(most_common, 80)}',
                    ],
                    metadata={
                        'source': self.source,
                        'window': self.window,
                        'repeated_count': count,
                    },
                    rule=self,
                    confidence_basis=(
                        f'most common state signature appears {count} times '
                        f'within a {self.window}-event window; threshold={self.threshold}'
                    ),
                )
            )
        return findings


@dataclass(frozen=True)
class StepCountLimitRule:
    """Flag a trajectory that exceeds a configured event budget."""

    max_steps: int = 50
    rule_id: str = 'core.trajectory.step_count_limit'
    rule_pack: str = 'core'
    priority: int = 120
    source: str = 'step_count_limit'

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        if len(trajectory.events) <= self.max_steps:
            return []
        target = trajectory.events[-1]
        mode = SEED_FAILURE_MODES['planning.inefficient_plan']
        return [
            _finding(
                mode=mode,
                event=target,
                evidence=[
                    f'{len(trajectory.events)} events exceeds configured '
                    f'max_steps={self.max_steps}',
                ],
                metadata={
                    'source': self.source,
                    'event_count': len(trajectory.events),
                },
                rule=self,
                confidence_basis=(
                    f'trajectory has {len(trajectory.events)} events; '
                    f'max_steps={self.max_steps}'
                ),
            )
        ]


def build_event_rules() -> List[object]:
    """Return default cross-framework single-event rules.

    The order is explicit via ``priority`` rather than list position. The
    broad explicit-error fallback intentionally stays early for compatibility;
    opt-in benchmark packs can use lower priorities to override it.
    """

    return [
        KeywordRule(
            rule_id='core.system.llm_limit',
            mode_id='system.llm_limit',
            needles=['context length', 'rate limit', 'token limit'],
            evidence='limit signal in event payload',
            confidence_basis='specific LLM limit phrase',
            priority=200,
        ),
        KeywordRule(
            rule_id='core.action.format_error',
            mode_id='action.format_error',
            needles=['json', 'schema', 'malformed', 'parse error'],
            evidence='format/schema signal in event payload',
            confidence_basis='format/schema keyword match',
            priority=300,
        ),
        KeywordRule(
            rule_id='core.action.parameter_error',
            mode_id='action.parameter_error',
            needles=['missing parameter', 'invalid parameter', 'hallucinated argument'],
            evidence='parameter signal in event payload',
            confidence_basis='parameter keyword match',
            priority=310,
        ),
        KeywordRule(
            rule_id='core.action.wrong_tool',
            mode_id='action.wrong_tool',
            needles=['unknown tool', 'wrong tool', 'tool mismatch'],
            evidence='tool-selection signal in event payload',
            confidence_basis='tool-selection keyword match',
            priority=320,
        ),
        KeywordRule(
            rule_id='core.planning.inefficient_plan',
            mode_id='planning.inefficient_plan',
            needles=['loop', 'repeated', 'no progress', 'max steps'],
            evidence='loop/progress signal in event payload',
            confidence_basis='loop/no-progress keyword match',
            priority=400,
        ),
        KeywordRule(
            rule_id='core.planning.constraint_ignorance',
            mode_id='planning.constraint_ignorance',
            needles=['policy violation', 'constraint ignored', 'must not'],
            evidence='constraint signal in event payload',
            confidence_basis='constraint keyword match',
            priority=410,
        ),
        KeywordRule(
            rule_id='core.verification.premature_stop',
            mode_id='verification.premature_stop',
            needles=['premature', 'early termination', 'not complete'],
            evidence='termination signal in event payload',
            confidence_basis='termination keyword match',
            priority=500,
        ),
        KeywordRule(
            rule_id='core.multiagent.handoff_loss',
            mode_id='multiagent.handoff_loss',
            needles=['handoff', 'lost context', 'missing context'],
            evidence='handoff/context signal in event payload',
            confidence_basis='handoff/context keyword match',
            priority=600,
        ),
        KeywordRule(
            rule_id='core.multimodal.perception_error',
            mode_id='multimodal.perception_error',
            needles=['ocr error', 'visual mismatch', 'ui element mismatch', 'perception error'],
            evidence='multimodal perception signal in event payload',
            confidence_basis='multimodal perception keyword match',
            priority=700,
        ),
        EventErrorRule(),
        MetadataStatusRule(),
    ]


@dataclass
class PrematureSuccessRule:
    """Flag a run that failed while the agent believed it had succeeded.

    The keyword rule ``core.verification.premature_stop`` only fires on explicit
    termination phrasing (``premature``/``early termination``/``not complete``).
    The common real case is the opposite: a run ends in failure, yet a reasoning
    or reflection step asserts the task is *done*. That mismatch between
    self-assessed completion and the actual failed outcome is a precise,
    low-false-positive signal because it only fires when the trajectory is
    explicitly marked failed.
    """

    rule_id: str = 'core.verification.premature_success'
    rule_pack: str = 'core'
    priority: int = 130
    source: str = 'premature_success'

    _claim_needles: tuple = (
        'task is complete',
        'task complete',
        'task is done',
        'task accomplished',
        'completed the task',
        'i have completed',
        'i am done',
        'we are done',
        'all done',
        'successfully completed',
        'no further action',
        'nothing left to do',
        'marking as complete',
        'is complete',
        'final answer',
    )

    _claim_event_types: tuple = (
        EventType.REFLECTION.value,
        EventType.OBSERVATION.value,
        EventType.LLM_RESPONSE.value,
        EventType.AGENT_STEP.value,
        EventType.PLAN.value,
    )

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        if not _run_failed(trajectory):
            return []
        target: Optional[AgentEvent] = None
        claim = ''
        for event in trajectory.events:
            if _event_type_value(event.event_type) not in self._claim_event_types:
                continue
            text = _claim_text(event)
            if _contains(text, self._claim_needles):
                target = event  # keep the last claim, closest to termination
                claim = next(n for n in self._claim_needles if n in text)
        if target is None:
            return []
        mode = SEED_FAILURE_MODES['verification.premature_stop']
        return [
            _finding(
                mode=mode,
                event=target,
                evidence=[
                    f'run ended in failure but {target.agent_name} asserted '
                    f'completion ("{claim}")',
                ],
                metadata={'source': self.source, 'claim_phrase': claim},
                rule=self,
                confidence_basis=(
                    'trajectory marked failed while a reasoning step claimed '
                    'the task was complete'
                ),
            )
        ]


def build_trajectory_rules() -> List[object]:
    """Return default cross-event rules in the core analysis pack."""

    return [
        RepeatedToolCallRule(),
        RepeatedStateRule(),
        StepCountLimitRule(),
        PrematureSuccessRule(),
    ]


def build_rules() -> List[object]:
    """Backward-compatible alias for the event-rule builder."""

    return build_event_rules()


def _contains(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def _run_failed(trajectory: AgentTrajectory) -> bool:
    """True only when the trajectory carries an explicit failure marker.

    Conservative by design: if ``finish_trace`` was never called there is no
    terminal marker and this returns False, so downstream rules that gate on it
    do not fire on partial/in-progress traces.
    """

    for event in trajectory.events:
        if event.metadata.get('success') is False:
            return True
        if _event_type_value(event.event_type) == EventType.ERROR.value and (
            event.agent_name == 'system'
        ):
            return True
    return False


def _claim_text(event: AgentEvent) -> str:
    return ' '.join(
        part for part in (_normalize(event.output), _normalize(event.error)) if part
    ).lower()


def _event_type_value(event_type: EventType) -> str:
    value = getattr(event_type, 'value', event_type)
    return str(value)


def _finding(
    *,
    mode: FailureMode,
    event: AgentEvent,
    evidence: List[str],
    metadata: dict[str, object],
    rule: object,
    confidence_basis: str,
) -> FailureFinding:
    enriched = dict(metadata)
    enriched.update(
        {
            'rule_pack': getattr(rule, 'rule_pack'),
            'rule_id': getattr(rule, 'rule_id'),
            'confidence_basis': confidence_basis,
            'analysis_layer': 'cross_event_rule',
            'finding_source_label': 'Cross-event rule',
            'trigger_scope': 'trajectory',
            'trigger_reason': confidence_basis,
            'why_reported': (
                f'This finding was emitted by deterministic rule '
                f'{getattr(rule, "rule_id")} after analyzing multiple events together.'
            ),
        }
    )
    return FailureFinding(
        finding_id=new_id('finding'),
        failure_mode=mode,
        event_id=event.event_id,
        agent_name=event.agent_name,
        step_index=event.step_index,
        confidence=None,
        evidence=evidence,
        suggestion=_suggestion(mode),
        metadata=enriched,
    )


def _state_signature(event: AgentEvent) -> str:
    """Return a compact, comparison-friendly view of observable state."""

    return '|'.join(
        [
            _event_type_value(event.event_type),
            str(event.agent_name or ''),
            str(event.module or ''),
            str(event.output or '')[:200],
            str(event.error or '')[:200],
        ]
    )


def _normalize(value: object) -> str:
    text = '' if value is None else str(value)
    return ' '.join(text.split())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) > max_chars:
        return text[:max_chars] + '...'
    return text


def _mode_count(items: List[str]) -> tuple[str, int]:
    counts: dict[str, int] = {}
    best = ''
    best_count = 0
    for item in items:
        counts[item] = counts.get(item, 0) + 1
        if counts[item] > best_count:
            best_count = counts[item]
            best = item
    return best, best_count


def _suggestion(mode: FailureMode) -> Optional[str]:
    if mode.suggestion_templates:
        return str(mode.suggestion_templates[0])
    return None
