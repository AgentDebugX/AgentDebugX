"""Trajectory analyzers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import List, Optional, Union

from agentdebug.diagnose.detect.rules import (
    load_event_rules,
    load_trajectory_rules,
    resolve_rule_pack_names,
)
from agentdebug.diagnose.detect.rules.base import EventRule, RuleMatch, TrajectoryRule
from agentdebug.schema import (
    SEED_FAILURE_MODES,
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
    confidence_or_default,
)


class HeuristicAnalyzer:
    """A deterministic baseline analyzer.

    This is intentionally simple: it gives projects an immediate local signal
    while future LLM judges, constraint synthesis, and learned attribution
    models share the same output schema.
    """

    def __init__(self, rule_packs: Optional[Union[str, List[str]]] = 'auto') -> None:
        self.rule_packs = rule_packs

    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        pack_names = resolve_rule_pack_names(self.rule_packs, trajectory)
        event_rules = load_event_rules(pack_names)
        trajectory_rules = load_trajectory_rules(pack_names)
        findings = [
            finding
            for event in trajectory.events
            for finding in self._event_findings(event, trajectory, event_rules)
        ]
        findings.extend(self._trajectory_findings(trajectory, trajectory_rules))
        # Build event-order map so the root selector can fall back to it when
        # findings lack step_index (e.g., events recorded via traced_tool on
        # pre-0.2.4 captures that pre-date the auto-counter).
        event_order = {evt.event_id: i for i, evt in enumerate(trajectory.events)}
        root = self._select_root_cause(findings, event_order=event_order)
        suggestions = self._dedupe(
            finding.suggestion for finding in findings if finding.suggestion is not None
        )

        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=findings,
            suggestions=suggestions,
            metadata={
                'analyzer': self.__class__.__name__,
                'rule_packs': pack_names,
                'event_rule_count': len(event_rules),
                'trajectory_rule_count': len(trajectory_rules),
            },
        )
        if root is not None:
            report.root_cause_event_id = root.event_id
            report.root_cause_agent = root.agent_name
            # If the finding lacked an explicit step_index, fall back to the
            # event's position so root_cause_step_index is non-null in the UI
            # and the AgentTraceback header reads sensibly.
            inferred_step = root.step_index
            if inferred_step is None and root.event_id is not None:
                pos = event_order.get(root.event_id)
                if pos is not None:
                    inferred_step = pos
            report.root_cause_step_index = inferred_step
            report.summary = (
                f'Likely root cause: {root.failure_mode.name}'
                f' in {root.agent_name or "unknown agent"}'
                f' at step {inferred_step if inferred_step is not None else "?"}.'
            )
        return report

    def _event_findings(
        self,
        event: AgentEvent,
        trajectory: AgentTrajectory,
        rules: List[EventRule],
    ) -> List[FailureFinding]:
        text = self._event_text(event)
        matched = self._match_failure_mode(event, text, trajectory, rules)
        if matched is None:
            return []

        failure_mode = SEED_FAILURE_MODES[matched.mode_id]
        metadata = self._finding_metadata(
            event=event,
            matched=matched,
        )
        metadata.update(matched.metadata)
        return [
            FailureFinding(
                failure_mode=failure_mode,
                event_id=event.event_id,
                agent_name=event.agent_name,
                step_index=event.step_index,
                confidence=matched.confidence,
                evidence=matched.evidence,
                suggestion=self._suggestion(failure_mode),
                metadata=metadata,
            )
        ]

    def _match_failure_mode(
        self,
        event: AgentEvent,
        text: str,
        trajectory: AgentTrajectory,
        rules: List[EventRule],
    ) -> Optional[RuleMatch]:
        for rule in rules:
            matched = rule.match(event, text, trajectory)
            if matched is not None:
                return matched
        return None

    def _finding_metadata(
        self,
        *,
        event: AgentEvent,
        matched: RuleMatch,
    ) -> dict[str, object]:
        event_type = self._event_type_value(event.event_type)
        return {
            'event_type': event_type,
            'source': 'heuristic_rule',
            'rule_pack': matched.rule_pack,
            'rule_id': matched.rule_id,
            'confidence_basis': matched.confidence_basis,
            'analysis_layer': 'single_event_rule',
            'finding_source_label': 'Single-event rule',
            'trigger_scope': 'event',
            'trigger_reason': matched.confidence_basis,
            'why_reported': (
                f'This event matched deterministic rule {matched.rule_id} '
                f'for event type {event_type}.'
            ),
        }

    def _trajectory_findings(
        self,
        trajectory: AgentTrajectory,
        rules: List[TrajectoryRule],
    ) -> List[FailureFinding]:
        findings: List[FailureFinding] = []
        for rule in rules:
            findings.extend(rule.detect(trajectory))
        return findings

    def _select_root_cause(
        self,
        findings: List[FailureFinding],
        *,
        event_order: Optional[dict[str, int]] = None,
    ) -> Optional[FailureFinding]:
        if not findings:
            return None
        # Primary key: step_index (None pushed to end).
        # Fallback: when step_index is None, use the event's position in the
        # trajectory so we still pick the *earliest* finding rather than just
        # the highest-confidence one.
        event_order = event_order or {}

        def _key(f: FailureFinding) -> tuple[int, int, int, float]:
            step = f.step_index if f.step_index is not None else 10**9
            order = (
                event_order.get(f.event_id, 10**9)
                if f.event_id is not None and f.step_index is None
                else 10**9
            )
            return (
                0 if f.step_index is not None else 1,
                step,
                order,
                -confidence_or_default(f.confidence),
            )

        return sorted(findings, key=_key)[0]

    def _event_text(self, event: AgentEvent) -> str:
        parts = [
            self._event_type_value(event.event_type),
            event.module or '',
            str(event.input or ''),
            str(event.output or ''),
            event.error or '',
            str(event.metadata),
        ]
        return '\n'.join(parts)

    def _suggestion(self, failure_mode: FailureMode) -> Optional[str]:
        if not failure_mode.suggestion_templates:
            return None
        return str(failure_mode.suggestion_templates[0])

    def _contains(self, text: str, needles: Iterable[str]) -> bool:
        return any(needle in text for needle in needles)

    def _dedupe(self, values: Iterable[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _event_type_value(self, event_type: EventType) -> str:
        value = getattr(event_type, 'value', event_type)
        return str(value)
