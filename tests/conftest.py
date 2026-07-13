from __future__ import annotations

import pytest

from agentdebug.schema import (
    AgentEvent,
    AgentTrajectory,
    DiagnosticReport,
    EventType,
    FailureFinding,
    FailureMode,
)


@pytest.fixture
def failure_mode() -> FailureMode:
    return FailureMode(
        mode_id='test.missing_constraint',
        name='Missing constraint',
        family='planning',
        description='A required constraint was dropped.',
        signals=['missing required parameter'],
        suggestion_templates=['Preserve and validate every required constraint.'],
        source='test',
    )


@pytest.fixture
def failed_trajectory() -> AgentTrajectory:
    trajectory = AgentTrajectory(
        trace_id='trace_failed',
        task_id='task_failed',
        goal='Book a refundable flight.',
        framework='test-framework',
        metadata={'suite': 'core'},
    )
    trajectory.add_event(
        AgentEvent(
            event_id='evt_plan',
            trace_id=trajectory.trace_id,
            agent_name='planner',
            event_type=EventType.PLAN,
            step_index=1,
            output='Search for the cheapest flight.',
        )
    )
    trajectory.add_event(
        AgentEvent(
            event_id='evt_tool',
            trace_id=trajectory.trace_id,
            parent_event_id='evt_plan',
            agent_name='browser',
            event_type=EventType.TOOL_RESULT,
            step_index=2,
            error='missing required parameter refund_policy',
            metadata={'attempt': 1},
        )
    )
    return trajectory


@pytest.fixture
def diagnostic_report(
    failed_trajectory: AgentTrajectory,
    failure_mode: FailureMode,
) -> DiagnosticReport:
    finding = FailureFinding(
        finding_id='finding_test',
        failure_mode=failure_mode,
        event_id='evt_plan',
        agent_name='planner',
        step_index=1,
        confidence=0.8,
        evidence=['The refundable constraint was omitted.'],
        suggestion='Preserve refund_policy before calling the browser.',
    )
    return DiagnosticReport(
        report_id='report_test',
        trace_id=failed_trajectory.trace_id,
        task_id=failed_trajectory.task_id,
        root_cause_event_id='evt_plan',
        root_cause_agent='planner',
        root_cause_step_index=1,
        findings=[finding],
        summary='The planner dropped a required constraint.',
        suggestions=['Preserve refund_policy before calling the browser.'],
    )
