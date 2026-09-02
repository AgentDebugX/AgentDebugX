"""ReferenceAttributor: a successful run of the same task, in context, told apart by id."""

from agentdebug.diagnose.attribute.reference import (
    ReferenceAttributor,
    build_reference_block,
    diff_traces,
    extract_actions,
    pick_reference,
)
from agentdebug.schema.models import AgentEvent, AgentTrajectory, EventType


def _traj(trace_id: str, tools: list) -> AgentTrajectory:
    t = AgentTrajectory(trace_id=trace_id, task_id='task_ref', goal='Book a refundable flight.')
    for i, (tool, args) in enumerate(tools):
        t.add_event(AgentEvent(
            event_id=f'{trace_id}_c{i}', trace_id=trace_id, agent_name='browser',
            event_type=EventType.TOOL_CALL, step_index=i, input={'tool': tool, 'args': args}))
        t.add_event(AgentEvent(
            event_id=f'{trace_id}_r{i}', trace_id=trace_id, agent_name='browser',
            event_type=EventType.TOOL_RESULT, step_index=i, output='ok'))
    return t


def test_extract_actions_reads_the_tool_call_shape() -> None:
    t = _traj('a', [('search', {'q': 'sfo'}), ('book', {'flight': 1})])
    acts = extract_actions(t)
    assert [(a.tool, a.step) for a in acts] == [('search', 0), ('book', 1)]


def test_diff_names_the_first_divergence_as_a_candidate() -> None:
    failed = _traj('f', [('search', {'q': 'sfo'}), ('book', {'flight': 1})])
    ref = _traj('r', [('search', {'q': 'sfo'}), ('check_refund', {}), ('book', {'flight': 1})])
    d = diff_traces(failed, ref)
    assert d.aligned_prefix == 1
    assert d.first_divergence_step == 1
    assert d.failed_action.tool == 'book' and d.reference_action.tool == 'check_refund'
    assert 1 in d.candidate_steps


def test_identical_runs_have_no_divergence() -> None:
    a = _traj('a', [('search', {}), ('book', {})])
    b = _traj('b', [('search', {}), ('book', {})])
    assert diff_traces(a, b).first_divergence_step is None


def test_pick_reference_prefers_the_longest_shared_prefix() -> None:
    failed = _traj('f', [('search', {}), ('filter', {}), ('book', {})])
    far = _traj('far', [('login', {})])
    near = _traj('near', [('search', {}), ('filter', {}), ('check_refund', {}), ('book', {})])
    assert pick_reference(failed, [far, near]) is near


def test_block_is_empty_with_nothing_to_say() -> None:
    assert build_reference_block(None) == ''


def test_block_says_a_reference_is_one_path_not_the_only_one() -> None:
    ref = _traj('r', [('search', {})])
    block = build_reference_block(ref)
    assert 'SUCCESSFUL run' in block
    assert 'not the only one' in block


def test_attributor_has_its_own_id_and_inherits_the_rest(failed_trajectory) -> None:
    class FakeLLM:
        def complete(self, messages, **kwargs):
            class R:
                text = '{"span_id": "evt_tool", "step_index": 2, "agent_name": "browser", ' \
                       '"confidence": 0.9, "rationale": "diverged from the reference", "evidence": []}'
                raw = {}
            return R()

    ref = _traj('r', [('search', {'q': 'refundable'})])
    attributor = ReferenceAttributor(FakeLLM(), ref, failed=failed_trajectory)
    assert attributor.id == 'with_reference_success'
    assert 'SUCCESSFUL run' in attributor.extra_context
    result = attributor.attribute(failed_trajectory)
    assert result.hypotheses
    assert result.hypotheses[0].sources == ['with_reference_success']
