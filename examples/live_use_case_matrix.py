"""Run several AgentDebugX use cases against a real OpenAI-compatible LLM.

This example is intentionally framework-neutral: it builds small traces that
look like failures seen in ReAct, LangGraph, CrewAI, AutoGen, OpenAI Agents,
and browser/tool agents, then runs the same diagnosis stack on each trace.

Default path requires a live LLM endpoint:

    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'
    export AGENTDEBUG_LLM_API_KEY='sk-...'
    export AGENTDEBUG_LLM_MODEL='gemini-3-flash'
    PYTHONPATH=src python examples/live_use_case_matrix.py

Cost controls:

    --cases tool_schema_error handoff_loss_cascade
    --with-step-by-step
    --with-deep
    --offline-only
    --json-out /tmp/agentdebug_live_use_cases.json
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from agentdebug import AgentDebug, AgentTrajectory, EventType
from agentdebug.analyzers import HeuristicAnalyzer
from agentdebug.attribution import (
    AllAtOnceAttributor,
    HeuristicAttributor,
    StepByStepAttributor,
)
from agentdebug.deep import DeepDebugAnalyzer
from agentdebug.judges import LLMJudgeAnalyzer
from agentdebug.llm import OpenAICompatClient
from agentdebug.recovery import CriticRecoverer, ReflexionSuggestion


@dataclass(frozen=True)
class UseCase:
    name: str
    title: str
    why: str
    expected_agent: Optional[str]
    expected_family: Optional[str]
    builder: Callable[[], AgentTrajectory]


def _finish(debugger: AgentDebug, trajectory: AgentTrajectory) -> AgentTrajectory:
    debugger.finish_trace(trajectory, success=False)
    return trajectory


def build_tool_schema_error() -> AgentTrajectory:
    debugger = AgentDebug()
    t = debugger.start_trace(
        goal='Find the latest AgentDebugX paper and summarize the method.',
        framework='raw-react',
    )
    debugger.record_event(
        t,
        EventType.PLAN,
        agent_name='agent',
        module='planning',
        step_index=1,
        output='Search the web, then summarize the newest paper.',
    )
    debugger.record_event(
        t,
        EventType.TOOL_CALL,
        agent_name='search_web',
        module='action',
        step_index=2,
        input={'tool': 'search_web', 'args': {}},
    )
    debugger.record_event(
        t,
        EventType.TOOL_RESULT,
        agent_name='search_web',
        module='action',
        step_index=3,
        error='JSON schema validation failed: missing required parameter query',
    )
    return _finish(debugger, t)


def build_handoff_loss_cascade() -> AgentTrajectory:
    debugger = AgentDebug()
    t = debugger.start_trace(
        goal='Book a refundable flight NYC to SFO under $400.',
        framework='multi-agent',
    )
    debugger.record_event(
        t,
        EventType.PLAN,
        agent_name='planner',
        module='planning',
        step_index=1,
        output='Plan: search NYC to SFO flights, pick the cheapest option, checkout. '
        'The plan omits the hard refundable constraint.',
    )
    debugger.record_event(
        t,
        EventType.HANDOFF,
        agent_name='planner',
        module='multiagent',
        step_index=2,
        output="Handoff to booking: 'find cheapest NYC to SFO flight'",
    )
    debugger.record_event(
        t,
        EventType.TOOL_RESULT,
        agent_name='search',
        module='action',
        step_index=3,
        input={'query': 'cheapest NYC SFO'},
        output='Found UA123 for $312, non-refundable.',
    )
    debugger.record_event(
        t,
        EventType.OBSERVATION,
        agent_name='booking',
        module='action',
        step_index=4,
        output='Selected UA123 because it is cheapest. Proceeding to checkout.',
    )
    debugger.record_event(
        t,
        EventType.TOOL_RESULT,
        agent_name='browser',
        module='action',
        step_index=5,
        error='Checkout validator rejected booking: refund_policy field missing.',
    )
    return _finish(debugger, t)


def build_premature_success() -> AgentTrajectory:
    debugger = AgentDebug()
    t = debugger.start_trace(
        goal='Return the paper title, URL, and one-sentence method summary.',
        framework='single-agent',
    )
    debugger.record_event(
        t,
        EventType.PLAN,
        agent_name='agent',
        module='planning',
        step_index=1,
        output='Use memory first; only search if memory is uncertain.',
    )
    debugger.record_event(
        t,
        EventType.LLM_RESPONSE,
        agent_name='agent',
        module='reasoning',
        step_index=2,
        output='I remember the paper is AgentDebugX, so no search is needed.',
    )
    debugger.record_event(
        t,
        EventType.REFLECTION,
        agent_name='agent',
        module='reflection',
        step_index=3,
        output='The task is complete even though no URL or method summary was verified.',
    )
    return _finish(debugger, t)


def build_repeated_tool_loop() -> AgentTrajectory:
    debugger = AgentDebug()
    t = debugger.start_trace(
        goal='Find a restaurant in Chicago that is open now and has vegan options.',
        framework='tool-agent',
    )
    for step in range(1, 5):
        debugger.record_event(
            t,
            EventType.TOOL_CALL,
            agent_name='search_web',
            module='action',
            step_index=step,
            input={'query': 'Chicago restaurant vegan open now'},
            output='No new constraints considered; repeating same query.',
        )
    debugger.record_event(
        t,
        EventType.ERROR,
        agent_name='agent',
        module='system',
        step_index=5,
        error='Step budget exceeded after repeated identical tool calls.',
    )
    return _finish(debugger, t)


USE_CASES: Dict[str, UseCase] = {
    'tool_schema_error': UseCase(
        name='tool_schema_error',
        title='Tool schema / missing parameter',
        why='Catches malformed tool arguments before downstream APIs fail.',
        expected_agent='search_web',
        expected_family='action',
        builder=build_tool_schema_error,
    ),
    'handoff_loss_cascade': UseCase(
        name='handoff_loss_cascade',
        title='Multi-agent handoff loss vs visible crash',
        why='Tests whether attribution moves upstream from a browser crash to the planner.',
        expected_agent='planner',
        expected_family='multiagent',
        builder=build_handoff_loss_cascade,
    ),
    'premature_success': UseCase(
        name='premature_success',
        title='Premature success / missing final verifier',
        why='Covers agents that stop with an unverified or incomplete answer.',
        expected_agent='agent',
        expected_family='verification',
        builder=build_premature_success,
    ),
    'repeated_tool_loop': UseCase(
        name='repeated_tool_loop',
        title='Repeated tool loop / no progress',
        why='Covers loop and step-budget failures in live tool agents.',
        expected_agent='search_web',
        expected_family='planning',
        builder=build_repeated_tool_loop,
    ),
}


def llm_from_env() -> OpenAICompatClient:
    missing = [
        key for key in ('AGENTDEBUG_LLM_BASE_URL', 'AGENTDEBUG_LLM_API_KEY')
        if not os.environ.get(key)
    ]
    if missing:
        raise RuntimeError(
            'Missing live LLM environment variables: ' + ', '.join(missing)
        )
    return OpenAICompatClient(
        base_url=os.environ['AGENTDEBUG_LLM_BASE_URL'],
        api_key=os.environ['AGENTDEBUG_LLM_API_KEY'],
        model=os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash'),
        default_max_tokens=int(os.environ.get('AGENTDEBUG_LLM_MAX_TOKENS', '8192')),
        timeout=float(os.environ.get('AGENTDEBUG_LLM_TIMEOUT', '180')),
    )


def _finding_families(report: Any) -> List[str]:
    return sorted({f.failure_mode.family for f in getattr(report, 'findings', [])})


def _top_blame(result: Any) -> Dict[str, Any]:
    hypotheses = getattr(result, 'hypotheses', []) or []
    if not hypotheses:
        return {}
    h = hypotheses[0]
    return {
        'agent': h.agent_name,
        'step': h.step_index,
        'confidence': h.confidence,
        'sources': h.sources,
    }


def run_use_case(
    use_case: UseCase,
    *,
    llm: Optional[OpenAICompatClient],
    with_step_by_step: bool = False,
    with_deep: bool = False,
) -> Dict[str, Any]:
    trajectory = use_case.builder()
    heuristic_report = HeuristicAnalyzer().analyze(trajectory)
    heuristic_attr = HeuristicAttributor().attribute(
        trajectory, heuristic_report.findings
    )
    out: Dict[str, Any] = {
        'case': use_case.name,
        'title': use_case.title,
        'why': use_case.why,
        'expected': {
            'agent': use_case.expected_agent,
            'family': use_case.expected_family,
        },
        'trace': {
            'trace_id': trajectory.trace_id,
            'event_count': len(trajectory.events),
        },
        'heuristic': {
            'root_agent': heuristic_report.root_cause_agent,
            'root_step': heuristic_report.root_cause_step_index,
            'families': _finding_families(heuristic_report),
            'top_blame': _top_blame(heuristic_attr),
        },
    }

    if llm is None:
        return out

    judge = LLMJudgeAnalyzer(llm=llm, max_tokens=8192).analyze(trajectory)
    all_once = AllAtOnceAttributor(llm=llm, max_tokens=4096).attribute(
        trajectory, judge.findings
    )
    out['llm_judge'] = {
        'root_agent': judge.root_cause_agent,
        'root_step': judge.root_cause_step_index,
        'families': _finding_families(judge),
        'finding_count': len(judge.findings),
        'summary': (judge.summary or '')[:240],
    }
    out['all_at_once'] = _top_blame(all_once)
    out['recovery'] = {
        'reflexion': len(ReflexionSuggestion().suggest(trajectory, judge)),
        'critic': len(CriticRecoverer().suggest(trajectory, judge)),
    }

    if with_step_by_step:
        step = StepByStepAttributor(llm=llm, max_steps=12, max_tokens=4096).attribute(
            trajectory, judge.findings
        )
        out['step_by_step'] = _top_blame(step)

    if with_deep:
        deep = DeepDebugAnalyzer(
            llm=llm,
            max_hypotheses_to_verify=3,
            max_tokens=4096,
        ).analyze(trajectory)
        out['deep_debug'] = {
            'root_agent': deep.root_cause_agent,
            'root_step': deep.root_cause_step_index,
            'finding_count': len(deep.findings),
            'round_count': len(deep.rounds),
            'summary': (deep.summary or '')[:240],
        }

    return out


def run_matrix(
    case_names: Iterable[str],
    *,
    offline_only: bool = False,
    with_step_by_step: bool = False,
    with_deep: bool = False,
) -> List[Dict[str, Any]]:
    llm = None if offline_only else llm_from_env()
    return [
        run_use_case(
            USE_CASES[name],
            llm=llm,
            with_step_by_step=with_step_by_step,
            with_deep=with_deep,
        )
        for name in case_names
    ]


def print_summary(rows: List[Dict[str, Any]]) -> None:
    print('case | heuristic root | llm judge root | all-at-once blame | families')
    print('--- | --- | --- | --- | ---')
    for row in rows:
        h = row['heuristic']
        judge = row.get('llm_judge', {})
        all_once = row.get('all_at_once', {})
        print(
            f"{row['case']} | "
            f"{h.get('root_agent')}@{h.get('root_step')} | "
            f"{judge.get('root_agent')}@{judge.get('root_step')} | "
            f"{all_once.get('agent')}@{all_once.get('step')} | "
            f"{judge.get('families') or h.get('families')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cases',
        nargs='+',
        choices=sorted(USE_CASES),
        default=list(USE_CASES),
        help='Use cases to run.',
    )
    parser.add_argument(
        '--offline-only',
        action='store_true',
        help='Run deterministic heuristic path only; no LLM calls.',
    )
    parser.add_argument(
        '--with-step-by-step',
        action='store_true',
        help='Also run StepByStepAttributor. More LLM calls.',
    )
    parser.add_argument(
        '--with-deep',
        action='store_true',
        help='Also run DeepDebugAnalyzer. Most expensive path.',
    )
    parser.add_argument('--json-out', type=Path, help='Write machine-readable results.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = run_matrix(
        args.cases,
        offline_only=args.offline_only,
        with_step_by_step=args.with_step_by_step,
        with_deep=args.with_deep,
    )
    print_summary(rows)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
