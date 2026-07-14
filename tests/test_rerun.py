from __future__ import annotations

import json
import sys

import pytest

from agentdebug.rerun import (
    LIVE_EXECUTION,
    LLMContinuationExecutor,
    ProcessLiveExecutor,
    RerunResult,
    RerunWorkflow,
    RolloutContext,
    SimulatedRerunExecutor,
    build_actor_rerun_task,
    build_rerun_request,
    export_actor_rerun_tasks,
    assess_rerun_capability,
    normalize_openai_base_url,
)
from agentdebug.runtime import CompletionResult
from agentdebug.rerun.branch import compare_branches
from agentdebug.rerun.evaluators import evaluate_local_proxy
from agentdebug.schema import AgentEvent, AgentTrajectory, DiagnosticReport, EventType


def _trajectory(trace_id: str, error_count: int) -> AgentTrajectory:
    trajectory = AgentTrajectory(trace_id=trace_id)
    for step in range(error_count):
        trajectory.add_event(
            AgentEvent(
                trace_id=trace_id,
                event_type=EventType.ERROR,
                step_index=step,
                error=f'error-{step}',
            )
        )
    return trajectory


@pytest.mark.parametrize(
    ('before', 'after', 'expected'),
    [(2, 1, 'improved'), (1, 1, 'unchanged'), (1, 2, 'worse')],
)
def test_branch_comparison_results(before: int, after: int, expected: str) -> None:
    comparison = compare_branches(
        _trajectory('before', before),
        _trajectory('after', after),
    )

    assert comparison.result == expected
    assert comparison.original_error_count == before
    assert comparison.rerun_error_count == after


def test_local_proxy_evaluation_scores_clean_rerun() -> None:
    evaluation = evaluate_local_proxy(
        _trajectory('before', 1),
        _trajectory('after', 0),
    )

    assert evaluation.result == 'improved'
    assert evaluation.score_before == 0
    assert evaluation.score_after == 1


def test_build_request_prefers_recovery_proposal(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.recovery = {
        'primary': {
            'recoverer_id': 'reflexion',
            'target_event_id': 'evt_plan',
            'summary': 'Correct the plan.',
            'rationale': 'The attributed plan omitted a constraint.',
            'suggestion_text': 'Use the approved recovery directive.',
        },
        'proposals': [
            {
                'suggestion_text': 'Use the approved recovery directive.',
                'requires_human_approval': False,
            }
        ]
    }

    request = build_rerun_request(diagnostic_report, failed_trajectory)

    assert request.checkpoint.event_id == 'evt_plan'
    assert request.directive.text == 'Use the approved recovery directive.'
    assert request.directive.requires_human_approval is False
    context = request.metadata['diagnostic_context']
    assert context['detect']['findings'][0]['event_id'] == 'evt_plan'
    assert context['root_cause']['event_id'] == 'evt_plan'
    assert context['recover']['suggestion_text'] == (
        'Use the approved recovery directive.'
    )


def test_rerun_request_carries_primary_attribution(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    diagnostic_report.attribution = {
        'method': 'test',
        'primary': {
            'span_id': 'evt_plan',
            'step_index': 1,
            'agent_name': 'planner',
            'rationale': 'The plan omitted refund_policy.',
            'evidence': ['Search for the cheapest flight.'],
            'sources': ['test'],
        },
    }

    request = build_rerun_request(diagnostic_report, failed_trajectory)

    attribution = request.metadata['diagnostic_context']['attribute']
    assert attribution['event_id'] == 'evt_plan'
    assert attribution['rationale'] == 'The plan omitted refund_policy.'
    assert attribution['evidence'] == ['Search for the cheapest flight.']


def test_suggest_only_workflow_never_executes(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    result = RerunWorkflow.suggest_only().run(
        diagnostic_report,
        failed_trajectory,
    )

    assert result.executed is False
    assert result.plan.status == 'not_executable'
    assert result.plan.capability.level == 'trajectory_only'
    assert 'framework_runner' in result.plan.capability.missing
    assert result.evaluation is None


def test_execution_requires_an_executor(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    with pytest.raises(ValueError, match='approved executor'):
        RerunWorkflow.suggest_only().run(
            diagnostic_report,
            failed_trajectory,
            execute=True,
        )


def test_replay_bundle_reports_remaining_live_requirements(
    failed_trajectory: AgentTrajectory,
) -> None:
    failed_trajectory.metadata['replay_bundle'] = {
        'environment_snapshot': 'docker://agentdebug/test:1',
        'tool_manifest': 'tools.json',
        'dependency_lock': 'poetry.lock',
    }

    capability = assess_rerun_capability(failed_trajectory)

    assert capability.level == 'replay_bundle'
    assert capability.executable is False
    assert 'replay_bundle' in capability.available
    assert capability.missing == ('framework_runner',)


def test_approved_executor_is_evaluated(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class Executor:
        id = 'test-executor'
        execution_mode = LIVE_EXECUTION

        def run(self, request):
            rerun = AgentTrajectory(trace_id='trace-rerun')
            rerun.add_event(
                AgentEvent(
                    trace_id=rerun.trace_id,
                    event_type=EventType.TOOL_RESULT,
                    output={'ok': True},
                )
            )
            return RerunResult(
                request=request,
                trajectory=rerun,
                metadata={
                    'executor': self.id,
                    'execution_mode': LIVE_EXECUTION,
                    'observed_execution': True,
                    'tools_executed': True,
                },
            )

    result = RerunWorkflow(Executor()).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
    )

    assert result.executed is True
    assert result.evaluation is not None
    assert result.evaluation.result == 'improved'
    assert result.to_dict()['execution']['trace_id'] == 'trace-rerun'


def test_request_without_suggestion_uses_safe_default() -> None:
    report = DiagnosticReport(trace_id='trace-empty', summary='No suggestion')

    request = build_rerun_request(report)

    assert request.trace_id == 'trace-empty'
    assert 'Inspect the evidence' in request.directive.text
    assert request.directive.requires_human_approval is True


def test_actor_rerun_task_is_pending_unlabeled_rollout_data(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    request = build_rerun_request(
        diagnostic_report,
        failed_trajectory,
        checkpoint_policy='from_start',
    )
    task = build_actor_rerun_task(request, diagnostic_report, failed_trajectory)
    output = export_actor_rerun_tasks(
        [task], tmp_path / 'actor-tasks.jsonl', format='jsonl'
    )
    record = json.loads(output.read_text(encoding='utf-8'))

    assert record['record_type'] == 'agentdebug.rerun.actor_task'
    assert record['schema_version'] == '1.0'
    assert record['status'] == 'pending'
    assert record['requires_live_environment'] is True
    assert record['verified'] is False
    assert record['checkpoint_policy'] == 'from_start'
    assert 'Preserve refund_policy' in record['retry_directive']
    assert record['messages'][0]['role'] == 'system'
    assert 'do not invent tool results' in record['messages'][0]['content']
    assert json.loads(record['source_trajectory_json'])['trace_id'] == (
        failed_trajectory.trace_id
    )
    assert json.loads(record['required_capabilities_json']) == [
        'framework_runner',
        'tool_runtime',
        'environment_state',
    ]
    assert 'response' not in record
    assert 'label' not in record


def test_actor_rerun_task_parquet_round_trip(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    pyarrow_parquet = pytest.importorskip('pyarrow.parquet')
    request = build_rerun_request(diagnostic_report, failed_trajectory)
    task = build_actor_rerun_task(request, diagnostic_report, failed_trajectory)
    output = export_actor_rerun_tasks(
        [task], tmp_path / 'actor-tasks.parquet', format='parquet'
    )

    record = pyarrow_parquet.read_table(output).to_pylist()[0]
    assert record['record_type'] == 'agentdebug.rerun.actor_task'
    assert record['status'] == 'pending'
    assert record['messages'][1]['role'] == 'user'


def test_llm_executor_generates_full_rerun_trajectory(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    assert LLMContinuationExecutor is SimulatedRerunExecutor

    class LLM:
        model = 'rerun-model'

        def complete(self, messages, **kwargs):
            assert 'Rerun policy: from_start' in messages[1]['content']
            assert 'Preserve refund_policy' in messages[1]['content']
            assert 'Diagnostic context (Detect -> Attribute -> Recover)' in (
                messages[1]['content']
            )
            assert 'test.missing_constraint' in messages[1]['content']
            return CompletionResult(
                text=(
                    '{"summary":"completed","success":true,"events":['
                    '{"agent_name":"planner","event_type":"plan",'
                    '"step_index":1,"output":"keep refund_policy"},'
                    '{"agent_name":"browser","event_type":"tool.result",'
                    '"step_index":2,"output":{"ok":true}}]}'
                ),
                raw={'usage': {'total_tokens': 10}},
            )

    executor = LLMContinuationExecutor(LLM(), RolloutContext(failed_trajectory))
    result = RerunWorkflow(executor, allow_simulated=True).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
        checkpoint_policy='from_start',
    )

    assert result.executed is True
    assert result.execution is not None
    assert [event.step_index for event in result.execution.trajectory.events] == [1, 2]
    assert result.execution.trajectory.metadata['rerun_of'] == 'trace_failed'
    assert result.execution.metadata['model_claimed_success'] is True
    assert result.execution.metadata['execution_mode'] == 'simulated_rollout'
    assert result.execution.metadata['tools_executed'] is False
    assert result.execution.metadata['artifact_type'] == 'hypothetical_trajectory'
    assert result.execution.metadata['executor'] == 'simulated_rerun'
    assert result.execution.metadata['verified'] is False
    assert result.execution.trajectory.events[0].metadata['simulated'] is True
    rendered = result.to_dict()
    assert rendered['status'] == 'simulated'
    assert rendered['execution_mode'] == 'simulated_rollout'
    assert rendered['live_execution'] is False
    assert rendered['verified'] is False
    assert rendered['evaluation']['verified_task_outcome'] is False


def test_rerun_cleans_failure_fixture_metadata_and_sets_ended_at(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    failed_trajectory.metadata.update(
        {
            'fixture': True,
            'scenario': 'constraint-loss',
            'expected_outcome': 'failure',
            'expected_root_cause_event_id': 'evt_plan',
            'expected_root_cause_step_index': 1,
            'expected_failure_mode': 'planning.constraint_ignorance',
            'failure_family': 'planning',
            'react_format': 'thought-action-observation',
            'business_context': 'travel',
        }
    )

    class LLM:
        model = 'rerun-model'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"summary":"completed","success":true,"events":['
                    '{"event_type":"run.start","step_index":0,"output":null},'
                    '{"event_type":"run.end","step_index":1,'
                    '"output":{"status":"success"}}]}'
                ),
                raw={},
            )

    result = RerunWorkflow(
        LLMContinuationExecutor(LLM(), RolloutContext(failed_trajectory)),
        allow_simulated=True,
    ).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
        checkpoint_policy='from_start',
    )

    assert result.execution is not None
    trajectory = result.execution.trajectory
    for key in (
        'fixture',
        'scenario',
        'expected_outcome',
        'expected_root_cause_event_id',
        'expected_root_cause_step_index',
        'expected_failure_mode',
        'failure_family',
    ):
        assert key not in trajectory.metadata
    assert trajectory.metadata['react_format'] == 'thought-action-observation'
    assert trajectory.metadata['business_context'] == 'travel'
    assert trajectory.ended_at == trajectory.events[-1].timestamp


def test_rerun_without_terminal_event_keeps_ended_at_unset(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class LLM:
        model = 'rerun-model'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"summary":"retry proposed","success":false,"events":['
                    '{"event_type":"agent.step","output":"retry"}]}'
                ),
                raw={},
            )

    result = RerunWorkflow(
        LLMContinuationExecutor(LLM(), RolloutContext(failed_trajectory)),
        allow_simulated=True,
    ).run(diagnostic_report, failed_trajectory, execute=True)

    assert result.execution is not None
    assert result.execution.trajectory.ended_at is None


def test_simulated_rerun_retries_truncated_output_with_more_tokens(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class LLM:
        model = 'rerun-model'

        def __init__(self) -> None:
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            if len(self.calls) == 1:
                return CompletionResult(
                    text='{"summary":"truncated",',
                    raw={'choices': [{'finish_reason': 'length'}]},
                )
            return CompletionResult(
                text=(
                    '{"summary":"hypothetical fix","success":true,"events":['
                    '{"event_type":"agent.step","output":"corrected"}]}'
                ),
                raw={'choices': [{'finish_reason': 'stop'}]},
            )

    llm = LLM()
    executor = SimulatedRerunExecutor(
        llm,
        RolloutContext(failed_trajectory),
        max_tokens=256,
    )
    result = RerunWorkflow(executor, allow_simulated=True).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
    )

    assert len(llm.calls) == 2
    assert llm.calls[0][1]['max_tokens'] == 256
    assert llm.calls[1][1]['max_tokens'] == 512
    assert 'previous response was truncated' in llm.calls[1][0][1]['content']
    assert result.execution is not None
    assert result.execution.metadata['generation_attempts'] == 2
    assert result.execution.metadata['final_token_budget'] == 512


def test_simulated_rerun_rejects_invalid_event_contract(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class LLM:
        model = 'rerun-model'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"summary":"invalid event","success":false,'
                    '"events":[{"output":"missing type"}]}'
                ),
                raw={'choices': [{'finish_reason': 'stop'}]},
            )

    with pytest.raises(ValueError, match=r'events\[0\]\.event_type is required'):
        RerunWorkflow(
            SimulatedRerunExecutor(
                LLM(),
                RolloutContext(failed_trajectory),
            ),
            allow_simulated=True,
        ).run(diagnostic_report, failed_trajectory, execute=True)


def test_simulated_rerun_retries_without_unsupported_response_format(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class LLM:
        model = 'rerun-model'

        def __init__(self) -> None:
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append(kwargs)
            if 'response_format' in kwargs:
                raise ValueError('response_format is unsupported')
            return CompletionResult(
                text=(
                    '{"summary":"hypothetical fix","success":true,"events":['
                    '{"event_type":"agent.step","output":"corrected"}]}'
                ),
                raw={'choices': [{'finish_reason': 'stop'}]},
            )

    llm = LLM()
    result = RerunWorkflow(
        SimulatedRerunExecutor(llm, RolloutContext(failed_trajectory)),
        allow_simulated=True,
    ).run(diagnostic_report, failed_trajectory, execute=True)

    assert len(llm.calls) == 2
    assert 'response_format' in llm.calls[0]
    assert 'response_format' not in llm.calls[1]
    assert result.execution is not None
    assert result.execution.metadata['output_validated'] is True


def test_checkpoint_rerun_parents_first_event_to_selected_event(
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    class LLM:
        model = 'rerun-model'

        def complete(self, messages, **kwargs):
            return CompletionResult(
                text=(
                    '{"summary":"retry proposed","success":false,"events":['
                    '{"event_type":"agent.step","output":"retry"}]}'
                ),
                raw={},
            )

    result = RerunWorkflow(
        LLMContinuationExecutor(
            LLM(),
            RolloutContext(failed_trajectory, start_event_id='evt_tool'),
        ),
        allow_simulated=True,
    ).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
        checkpoint_policy='from_event',
        checkpoint_event_id='evt_tool',
    )

    assert result.execution is not None
    assert result.plan.request.checkpoint.event_id == 'evt_tool'
    assert result.execution.trajectory.events[0].parent_event_id == 'evt_tool'


def test_live_process_executor_runs_framework_runner(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    runner = tmp_path / 'runner.py'
    runner.write_text(
        '\n'.join(
            [
                'import json, os',
                'request = json.load(open(os.environ["AGENTDEBUG_RERUN_REQUEST"]))',
                'source = json.load(open(os.environ["AGENTDEBUG_RERUN_SOURCE"]))',
                'trace_id = source["trace_id"] + "__live"',
                'result = {',
                '  "execution": {"mode": "live_execution",',
                '    "observed_execution": True, "tools_executed": True,',
                '    "tool_execution_count": 1, "runner": "test.runner",',
                '    "framework": "test-framework"},',
                '  "trajectory": {"trace_id": trace_id, "goal": source.get("goal"),',
                '    "framework": "test-framework", "events": [',
                '      {"trace_id": trace_id, "event_type": "tool.call",',
                '       "agent_name": "browser", "step_index": 1,',
                '       "input": {"refund_policy": "refundable"}},',
                '      {"trace_id": trace_id, "event_type": "tool.result",',
                '       "agent_name": "browser", "step_index": 2,',
                '       "output": {"booking": "confirmed"}}]},',
                '  "metadata": {"summary": "live tool execution completed",',
                '    "directive_seen": request["directive"]["text"]}',
                '}',
                'json.dump(result, open(os.environ["AGENTDEBUG_RERUN_OUTPUT"], "w"))',
            ]
        ),
        encoding='utf-8',
    )
    executor = ProcessLiveExecutor(
        [sys.executable, str(runner)],
        failed_trajectory,
    )

    result = RerunWorkflow(executor).run(
        diagnostic_report,
        failed_trajectory,
        execute=True,
        checkpoint_policy='from_start',
    )

    assert result.execution is not None
    assert result.plan.capability.level == 'live_runner'
    assert result.execution.execution_mode == LIVE_EXECUTION
    assert result.execution.metadata['tool_execution_count'] == 1
    assert result.execution.trajectory.metadata['tools_executed'] is True
    assert result.execution.trajectory.events[-1].output == {
        'booking': 'confirmed'
    }


def test_live_process_executor_accepts_observed_tool_free_agent(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    runner = tmp_path / 'runner.py'
    runner.write_text(
        'import json, os\n'
        'json.dump({"execution":{"mode":"live_execution",'
        '"observed_execution":True,"tools_executed":False,'
        '"runner":"tool-free","framework":"test",'
        '"tool_execution_count":0},"trajectory":{"trace_id":"fake",'
        '"events":[{"trace_id":"fake","event_type":"llm.response",'
        '"output":"completed without tools"}]}},'
        'open(os.environ["AGENTDEBUG_RERUN_OUTPUT"],"w"))\n',
        encoding='utf-8',
    )

    result = RerunWorkflow(
        ProcessLiveExecutor([sys.executable, str(runner)], failed_trajectory)
    ).run(diagnostic_report, failed_trajectory, execute=True)

    assert result.execution is not None
    assert result.execution.metadata['observed_execution'] is True
    assert result.execution.metadata['tools_executed'] is False


def test_live_process_executor_rejects_inconsistent_tool_proof(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    diagnostic_report: DiagnosticReport,
) -> None:
    runner = tmp_path / 'false_proof.py'
    runner.write_text(
        'import json, os\n'
        'json.dump({"execution":{"mode":"live_execution",'
        '"observed_execution":True,"tools_executed":True,'
        '"runner":"fake","framework":"test","tool_execution_count":0},'
        '"trajectory":{"trace_id":"fake","events":['
        '{"trace_id":"fake","event_type":"llm.response","output":"done"}]}},'
        'open(os.environ["AGENTDEBUG_RERUN_OUTPUT"],"w"))\n',
        encoding='utf-8',
    )

    with pytest.raises(ValueError, match='must match tool_execution_count'):
        RerunWorkflow(
            ProcessLiveExecutor([sys.executable, str(runner)], failed_trajectory)
        ).run(diagnostic_report, failed_trajectory, execute=True)


def test_openai_base_url_accepts_full_chat_endpoint() -> None:
    assert normalize_openai_base_url('https://host/v1') == 'https://host/v1'
    assert (
        normalize_openai_base_url('https://host/v1/chat/completions')
        == 'https://host/v1'
    )
