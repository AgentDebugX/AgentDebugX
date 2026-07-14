from __future__ import annotations

from agentdebug.diagnose.attribute.moe import analyze_aao_moe
from agentdebug.diagnose.profiles import DeepDebugAnalyzer
from agentdebug.runtime import CompletionResult
from agentdebug.schema import AgentEvent, AgentTrajectory, EventType


class PaperFlowLLM:
    model = 'paper-flow-test'

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, messages, **kwargs):
        system = str(messages[0]['content'])
        self.calls.append(system)
        if 'final, human-readable diagnosis' in system:
            payload = (
                '{"summary":"The planner dropped the required constraint.",'
                '"evidence":[{"event_id":"evt_1",'
                '"quote":"constraint omitted"}],'
                '"suggestion":"Preserve the constraint before tool use."}'
            )
        elif 'split into UPPER and LOWER halves' in system:
            payload = '{"half":"upper","confidence":0.9}'
        elif 'DECISIVE ROOT-CAUSE' in system:
            payload = (
                '{"event_id":"evt_1","agent":"planner",'
                '"step":1,"confidence":0.8}'
            )
        else:
            payload = (
                '{"span_id":"evt_1","step_index":1,"agent_name":"planner",'
                '"confidence":0.85,"rationale":"first wrong decision",'
                '"evidence":["constraint omitted"]}'
            )
        return CompletionResult(text=payload, raw={})


def _multi_agent_trajectory() -> AgentTrajectory:
    trajectory = AgentTrajectory(
        trace_id='trace_deepdebug',
        goal='Complete the task while preserving every constraint.',
        framework='test',
    )
    for step in range(1, 9):
        trajectory.add_event(
            AgentEvent(
                event_id=f'evt_{step}',
                trace_id=trajectory.trace_id,
                agent_name='planner' if step % 2 else 'executor',
                event_type=EventType.AGENT_STEP,
                step_index=step,
                output=(
                    'constraint omitted at the initial decision'
                    if step == 1
                    else f'downstream step {step}'
                ),
            )
        )
    return trajectory


def test_typed_analysis_preserves_legacy_mapping() -> None:
    analysis = analyze_aao_moe(_multi_agent_trajectory(), llm=PaperFlowLLM())
    legacy = analysis.as_legacy_dict()

    assert analysis.global_read.step_index == 1
    assert analysis.structure_probe.strategy == 'cascade'
    assert analysis.structure_probe.decisions[0].selected_half == 'upper'
    assert analysis.structure_probe.final_window == [1, 2, 3, 4]
    assert analysis.adjudication.agreed is True
    assert legacy['step_index'] == 1
    assert legacy['raw']['verdict'] == 'agreement'


def test_deepdebug_records_the_four_paper_stages() -> None:
    llm = PaperFlowLLM()

    result = DeepDebugAnalyzer(llm=llm).analyze(_multi_agent_trajectory())

    assert [round_.name for round_ in result.rounds] == [
        'global_read',
        'structure_probe',
        'cross_examine',
        'diagnose_and_suggest',
    ]
    assert result.analysis is not None
    assert result.analysis.structure_probe.decisions
    assert result.rounds[1].payload['decisions'][0]['selected_half'] == 'upper'
    assert result.rounds[2].payload['agreed'] is True
    assert result.diagnosis is not None
    assert result.diagnosis.suggestion == result.report.suggestions[0]
    assert result.report.root_cause_event_id == 'evt_1'
    assert result.report.metadata['deepdebug_stages'] == [
        round_.name for round_ in result.rounds
    ]
    assert len(llm.calls) == 4


class DuplicateStepLLM(PaperFlowLLM):
    def complete(self, messages, **kwargs):
        system = str(messages[0]['content'])
        self.calls.append(system)
        if 'final, human-readable diagnosis' in system:
            payload = (
                '{"summary":"The planner made the decisive mistake.",'
                '"evidence":[{"event_id":"evt_1",'
                '"quote":"this quote does not exist"}],'
                '"suggestion":"Preserve the original constraint."}'
            )
        elif 'split into UPPER and LOWER halves' in system:
            payload = '{"half":"upper","confidence":0.9}'
        elif 'Two candidate events' in system:
            payload = '{"candidate":"A","event_id":"evt_1"}'
        elif 'DECISIVE ROOT-CAUSE' in system:
            payload = (
                '{"event_id":"evt_duplicate","agent":"executor",'
                '"step":1,"confidence":0.8}'
            )
        else:
            payload = (
                '{"span_id":"evt_1","step_index":1,"agent_name":"planner",'
                '"confidence":0.85,"rationale":"first wrong decision",'
                '"evidence":["constraint omitted"]}'
            )
        return CompletionResult(text=payload, raw={})


def test_duplicate_step_uses_event_identity_and_rejects_hallucinated_evidence() -> None:
    trajectory = _multi_agent_trajectory()
    trajectory.events.insert(
        1,
        AgentEvent(
            event_id='evt_duplicate',
            trace_id=trajectory.trace_id,
            agent_name='executor',
            event_type=EventType.AGENT_STEP,
            step_index=1,
            output='executor only forwarded the planner decision',
        ),
    )

    result = DeepDebugAnalyzer(llm=DuplicateStepLLM()).analyze(trajectory)

    assert result.analysis is not None
    assert result.analysis.global_read.event_id == 'evt_1'
    assert result.analysis.structure_probe.candidate.event_id == 'evt_duplicate'
    assert result.analysis.adjudication.verdict == 'arbitrate->aao'
    assert result.report.root_cause_event_id == 'evt_1'
    assert result.report.root_cause_agent == 'planner'
    assert result.diagnosis is not None
    assert result.diagnosis.rejected_evidence_count == 1
    assert result.diagnosis.evidence_references[0].event_id == 'evt_1'
    assert result.report.findings[0].evidence == [
        'constraint omitted at the initial decision'
    ]
    assert result.report.metadata['evidence_verified'] is True
