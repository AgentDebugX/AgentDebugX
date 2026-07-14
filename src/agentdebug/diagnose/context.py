"""Shared state passed across Detect, Attribute, and Recover."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Optional

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    FailureFinding,
    FailureMode,
)


@dataclass(frozen=True)
class DiagnoseContext:
    """Preserve detector output while exposing attribution as the recovery target."""

    trajectory: AgentTrajectory
    report: DiagnosticReport
    recovery_report: DiagnosticReport
    primary_attribution: Optional[dict[str, Any]] = None
    recovery_target: Optional[FailureFinding] = None

    @classmethod
    def build(
        cls,
        trajectory: AgentTrajectory,
        report: DiagnosticReport,
        attribution: Optional[Any] = None,
    ) -> 'DiagnoseContext':
        primary = _primary_attribution(attribution, report.attribution)
        event = _resolve_attributed_event(trajectory, primary)
        target = _build_recovery_target(report, event, primary)
        recovery_report = deepcopy(report)

        if target is not None:
            recovery_report.findings = [target]
            rationale = str((primary or {}).get('rationale') or '').strip()
            if rationale:
                recovery_report.summary = rationale
            for target_report in (report, recovery_report):
                target_report.root_cause_event_id = target.event_id
                target_report.root_cause_agent = target.agent_name
                target_report.root_cause_step_index = target.step_index
            report.metadata['recovery_target'] = {
                'source': 'primary_attribution',
                'event_id': target.event_id,
                'agent_name': target.agent_name,
                'step_index': target.step_index,
                'finding_id': target.finding_id,
            }

        upstream_detect = report.metadata.get('upstream_detect')
        detect_context = (
            dict(upstream_detect)
            if isinstance(upstream_detect, dict)
            else {
                'summary': report.summary,
                'findings': [_finding_context(finding) for finding in report.findings],
            }
        )
        recovery_report.metadata['diagnose_context'] = {
            'detect': detect_context,
            'attribute': primary,
            'recovery_target': _finding_context(target) if target is not None else None,
        }

        return cls(
            trajectory=trajectory,
            report=report,
            recovery_report=recovery_report,
            primary_attribution=primary,
            recovery_target=target,
        )


def _primary_attribution(
    attribution: Optional[Any],
    payload: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    hypotheses = getattr(attribution, 'hypotheses', None)
    if hypotheses:
        return _as_payload(hypotheses[0])
    if not isinstance(payload, dict):
        return None
    primary = payload.get('primary')
    if isinstance(primary, dict):
        return dict(primary)
    payload_hypotheses = payload.get('hypotheses')
    if isinstance(payload_hypotheses, list) and payload_hypotheses:
        first = payload_hypotheses[0]
        if isinstance(first, dict):
            return dict(first)
    return None


def _as_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {
        key: getattr(value, key)
        for key in (
            'span_id',
            'event_id',
            'step_index',
            'agent_name',
            'confidence',
            'rationale',
            'evidence',
            'sources',
        )
        if hasattr(value, key)
    }


def _resolve_attributed_event(
    trajectory: AgentTrajectory,
    primary: Optional[dict[str, Any]],
) -> Optional[AgentEvent]:
    if not primary:
        return None
    event_id = str(primary.get('span_id') or primary.get('event_id') or '').strip()
    if event_id:
        event = next((item for item in trajectory.events if item.event_id == event_id), None)
        if event is not None:
            return event

    step_index = primary.get('step_index')
    agent_name = str(primary.get('agent_name') or '').strip()
    candidates = [
        event
        for event in trajectory.events
        if step_index is not None
        and event.step_index == step_index
        and (not agent_name or event.agent_name == agent_name)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _build_recovery_target(
    report: DiagnosticReport,
    event: Optional[AgentEvent],
    primary: Optional[dict[str, Any]],
) -> Optional[FailureFinding]:
    if event is None:
        return None
    if not report.findings:
        return FailureFinding(
            failure_mode=FailureMode(
                mode_id='attribution.root_cause',
                name='Attributed root cause',
                family='attribution',
                description='Root cause localized by the attribution stage.',
            ),
            event_id=event.event_id,
            agent_name=event.agent_name,
            step_index=event.step_index,
            confidence=_numeric_confidence(primary),
            evidence=[str(item) for item in (primary or {}).get('evidence') or []],
            metadata={
                'recovery_target_source': 'primary_attribution',
                'attribution_rationale': str((primary or {}).get('rationale') or ''),
                'attribution_sources': list((primary or {}).get('sources') or []),
            },
        )
    source = next(
        (finding for finding in report.findings if finding.event_id == event.event_id),
        None,
    )
    if source is None:
        source = next(
            (
                finding
                for finding in report.findings
                if finding.step_index == event.step_index
                and (
                    not finding.agent_name
                    or not event.agent_name
                    or finding.agent_name == event.agent_name
                )
            ),
            report.findings[0],
        )

    target = deepcopy(source)
    target.event_id = event.event_id
    target.agent_name = event.agent_name
    target.step_index = event.step_index
    attribution_evidence = (primary or {}).get('evidence')
    if isinstance(attribution_evidence, list) and attribution_evidence:
        target.evidence = [str(item) for item in attribution_evidence]
    confidence = _numeric_confidence(primary)
    if confidence is not None:
        target.confidence = confidence
    target.metadata = {
        **target.metadata,
        'recovery_target_source': 'primary_attribution',
        'detected_finding_id': source.finding_id,
        'attribution_rationale': str((primary or {}).get('rationale') or ''),
        'attribution_sources': list((primary or {}).get('sources') or []),
    }
    return target


def _numeric_confidence(primary: Optional[dict[str, Any]]) -> Optional[float]:
    confidence = (primary or {}).get('confidence')
    return float(confidence) if isinstance(confidence, (int, float)) else None


def _finding_context(finding: FailureFinding) -> dict[str, Any]:
    return {
        'finding_id': finding.finding_id,
        'failure_mode_id': finding.failure_mode.mode_id,
        'failure_mode_name': finding.failure_mode.name,
        'event_id': finding.event_id,
        'agent_name': finding.agent_name,
        'step_index': finding.step_index,
        'evidence': list(finding.evidence),
        'suggestion': finding.suggestion,
    }


__all__ = ['DiagnoseContext']
